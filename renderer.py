from __future__ import annotations

import asyncio
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from models import CameraKeyframe, ShotPlan


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
        browser = await playwright.chromium.launch(headless=options.headless)
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
                    "initialCamera": {
                        "lat": shot.keyframes[0].lat,
                        "lng": shot.keyframes[0].lng,
                        "alt_m": shot.keyframes[0].alt_m,
                        "heading_deg": shot.keyframes[0].heading_deg,
                        "tilt_deg": shot.keyframes[0].tilt_deg,
                    },
                },
            )
        except PlaywrightError as exc:
            await browser.close()
            console_tail = "\n".join(console_messages[-8:])
            extra = f"\n브라우저 콘솔:\n{console_tail}" if console_tail else ""
            raise RuntimeError(
                "Cesium/Google 3D Tiles 초기화 실패. "
                "API 키 유효성, Map Tiles API 활성화, 결제(Billing) 활성화, "
                "API 키 제한(referrer/IP) 설정을 확인하세요.\n"
                f"원본 오류: {exc}{extra}"
            ) from exc

        await page.wait_for_timeout(1_000)

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
        raise ValueError("Google Maps API 키가 필요합니다. --google-api-key 또는 GOOGLE_MAPS_API_KEY를 설정하세요.")

    if viewer_html_path is None:
        viewer_html_path = Path(__file__).resolve().parent / "web" / "viewer.html"

    if not viewer_html_path.exists():
        raise FileNotFoundError(f"viewer HTML 파일이 없습니다: {viewer_html_path}")

    return asyncio.run(_render_shot_async(shot, frame_dir, options, viewer_html_path))
