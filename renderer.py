from __future__ import annotations

import asyncio
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from models import CameraKeyframe, ShotPlan

_GPU_ARGS = [
    "--enable-gpu",
    "--enable-webgl",
    "--ignore-gpu-blocklist",
    "--enable-gpu-rasterization",
    "--disable-software-rasterizer",
]


@dataclass
class RenderOptions:
    width: int
    height: int
    fps: int
    google_api_key: str
    max_frames: int | None = None
    headless: bool = True


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_angle_deg(a: float, b: float, t: float) -> float:
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return (a + delta * t + 360.0) % 360.0


def _interpolate_state(keyframes: list[CameraKeyframe], t: float) -> dict:
    if t <= keyframes[0].t:
        k = keyframes[0]
        return {
            "lat": k.lat,
            "lng": k.lng,
            "alt_m": k.alt_m,
            "heading_deg": k.heading_deg,
            "tilt_deg": k.tilt_deg,
        }

    times = [k.t for k in keyframes]
    idx = bisect_right(times, t)
    if idx >= len(keyframes):
        k = keyframes[-1]
        return {
            "lat": k.lat,
            "lng": k.lng,
            "alt_m": k.alt_m,
            "heading_deg": k.heading_deg,
            "tilt_deg": k.tilt_deg,
        }

    a = keyframes[idx - 1]
    b = keyframes[idx]
    span = max(b.t - a.t, 1e-8)
    p = (t - a.t) / span
    return {
        "lat": _lerp(a.lat, b.lat, p),
        "lng": _lerp(a.lng, b.lng, p),
        "alt_m": _lerp(a.alt_m, b.alt_m, p),
        "heading_deg": _lerp_angle_deg(a.heading_deg, b.heading_deg, p),
        "tilt_deg": _lerp(a.tilt_deg, b.tilt_deg, p),
    }


def _kf_to_dict(k: CameraKeyframe) -> dict:
    return {
        "lat": k.lat,
        "lng": k.lng,
        "alt_m": k.alt_m,
        "heading_deg": k.heading_deg,
        "tilt_deg": k.tilt_deg,
    }


# ─── Single-shot rendering (backward-compatible) ──────────────────────


async def _render_shot_async(
    shot: ShotPlan,
    frame_dir: Path,
    options: RenderOptions,
    viewer_html_path: Path,
) -> int:
    frame_dir.mkdir(parents=True, exist_ok=True)
    for p in frame_dir.glob("frame_*.png"):
        p.unlink()

    total_frames = shot.duration_sec * options.fps
    if options.max_frames is not None:
        total_frames = min(total_frames, options.max_frames)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=options.headless, args=_GPU_ARGS,
        )
        page = await browser.new_page(viewport={"width": options.width, "height": options.height})
        console_messages: list[str] = []

        def _on_console(msg) -> None:
            text = msg.text.strip()
            if text:
                console_messages.append(f"[{msg.type}] {text}")

        page.on("console", _on_console)
        await page.goto(viewer_html_path.as_uri(), wait_until="networkidle")

        try:
            await page.evaluate(
                "async (cfg) => { await window.bootRenderer(cfg); }",
                {
                    "googleApiKey": options.google_api_key,
                    "initialCamera": _kf_to_dict(shot.keyframes[0]),
                },
            )
        except PlaywrightError as exc:
            await browser.close()
            console_tail = "\n".join(console_messages[-8:])
            extra = f"\nBrowser console:\n{console_tail}" if console_tail else ""
            raise RuntimeError(
                "Cesium/Google 3D Tiles initialization failed. "
                "Check API key validity, Map Tiles API enabled, billing enabled, "
                "and API key restrictions (referrer/IP).\n"
                f"Original error: {exc}{extra}"
            ) from exc

        await page.wait_for_timeout(1_000)

        # Preload tiles at keyframe positions before capturing
        kf_positions = [_kf_to_dict(k) for k in shot.keyframes]
        await page.evaluate(
            "async (pos) => { await window.preloadPositions(pos); }",
            kf_positions,
        )

        for i in range(total_frames):
            t = i / options.fps
            state = _interpolate_state(shot.keyframes, t)
            await page.evaluate("(state) => window.setCameraState(state);", state)
            await page.evaluate("async () => { await window.renderOnce(); }")
            await page.screenshot(path=str(frame_dir / f"frame_{i:06d}.png"), type="png")

        await browser.close()

    return total_frames


def render_shot_frames(
    shot: ShotPlan,
    frame_dir: Path,
    options: RenderOptions,
    viewer_html_path: Path | None = None,
) -> int:
    if not options.google_api_key:
        raise ValueError("Google Maps API key is required. Set --google-api-key or GOOGLE_MAPS_API_KEY.")

    if viewer_html_path is None:
        viewer_html_path = Path(__file__).resolve().parent / "web" / "viewer.html"

    if not viewer_html_path.exists():
        raise FileNotFoundError(f"Viewer HTML file not found: {viewer_html_path}")

    return asyncio.run(_render_shot_async(shot, frame_dir, options, viewer_html_path))


# ─── Batch rendering (browser reuse across shots) ─────────────────────


class BatchRenderer:
    """Reusable browser session that renders multiple shots without
    restarting Chromium. Tiles cached in the browser stay warm across shots,
    dramatically reducing network wait time for the 2nd+ shot."""

    def __init__(
        self,
        options: RenderOptions,
        viewer_html_path: Path | None = None,
    ):
        self._options = options
        self._vpath = viewer_html_path or (
            Path(__file__).resolve().parent / "web" / "viewer.html"
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pw = None
        self._browser = None
        self._page = None
        self._console_messages: list[str] = []

    # ── context manager ──

    def __enter__(self):
        if not self._options.google_api_key:
            raise ValueError("Google Maps API key is required.")
        if not self._vpath.exists():
            raise FileNotFoundError(f"Viewer HTML not found: {self._vpath}")

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._pw = self._loop.run_until_complete(async_playwright().start())
        self._browser = self._loop.run_until_complete(
            self._pw.chromium.launch(headless=self._options.headless, args=_GPU_ARGS)
        )
        self._page = self._loop.run_until_complete(
            self._browser.new_page(
                viewport={"width": self._options.width, "height": self._options.height}
            )
        )

        self._loop.run_until_complete(
            self._page.goto(self._vpath.as_uri(), wait_until="networkidle")
        )
        return self

    def __exit__(self, *exc_info):
        if self._browser:
            self._loop.run_until_complete(self._browser.close())
        if self._pw:
            self._loop.run_until_complete(self._pw.stop())
        if self._loop:
            self._loop.close()

    # ── public API ──

    def boot(self, initial_keyframe: CameraKeyframe):
        """Initialize CesiumJS + Google 3D Tiles (call once after __enter__)."""
        try:
            self._loop.run_until_complete(
                self._page.evaluate(
                    "async (cfg) => { await window.bootRenderer(cfg); }",
                    {
                        "googleApiKey": self._options.google_api_key,
                        "initialCamera": _kf_to_dict(initial_keyframe),
                    },
                )
            )
        except PlaywrightError as exc:
            console_tail = "\n".join(self._console_messages[-8:])
            extra = f"\nBrowser console:\n{console_tail}" if console_tail else ""
            raise RuntimeError(
                "Cesium/Google 3D Tiles initialization failed. "
                "Check API key validity, Map Tiles API enabled, billing enabled.\n"
                f"Original error: {exc}{extra}"
            ) from exc

        self._loop.run_until_complete(self._page.wait_for_timeout(1_000))

    def render_shot(self, shot: ShotPlan, frame_dir: Path) -> int:
        """Render one shot (preload + capture). Browser stays alive after."""
        return self._loop.run_until_complete(self._capture(shot, frame_dir))

    def render_still(
        self,
        shot: ShotPlan,
        image_path: Path,
        t_sec: float | None = None,
    ) -> None:
        """Render one still image from a shot while reusing the browser session."""
        self._loop.run_until_complete(self._capture_still(shot, image_path, t_sec))

    # ── internals ──

    async def _capture(self, shot: ShotPlan, frame_dir: Path) -> int:
        frame_dir.mkdir(parents=True, exist_ok=True)
        for p in frame_dir.glob("frame_*.png"):
            p.unlink()

        total_frames = shot.duration_sec * self._options.fps
        if self._options.max_frames is not None:
            total_frames = min(total_frames, self._options.max_frames)

        kf_positions = [_kf_to_dict(k) for k in shot.keyframes]
        await self._page.evaluate(
            "async (pos) => { await window.preloadPositions(pos); }",
            kf_positions,
        )

        for i in range(total_frames):
            t = i / self._options.fps
            state = _interpolate_state(shot.keyframes, t)
            await self._page.evaluate("(s) => window.setCameraState(s);", state)
            await self._page.evaluate("async () => { await window.renderOnce(); }")
            await self._page.screenshot(
                path=str(frame_dir / f"frame_{i:06d}.png"), type="png",
            )

        return total_frames

    async def _capture_still(
        self,
        shot: ShotPlan,
        image_path: Path,
        t_sec: float | None,
    ) -> None:
        image_path.parent.mkdir(parents=True, exist_ok=True)

        kf_positions = [_kf_to_dict(k) for k in shot.keyframes]
        await self._page.evaluate(
            "async (pos) => { await window.preloadPositions(pos); }",
            kf_positions,
        )

        if t_sec is None:
            t_sec = shot.duration_sec * 0.5
        t_sec = max(0.0, min(float(t_sec), float(shot.duration_sec)))
        state = _interpolate_state(shot.keyframes, t_sec)

        await self._page.evaluate("(s) => window.setCameraState(s);", state)
        await self._page.evaluate("async () => { await window.renderOnce(); }")
        await self._page.screenshot(path=str(image_path), type="png")
