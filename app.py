from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageOps

from camera_path import generate_shot_plans
from encoder import encode_frames_to_mp4
from geocoder import geocode
from jsx_exporter import export_jsx
from kml_exporter import export_kml
from models import CameraKeyframe, ShotPlan
from recommender import build_shot_analysis

app = Flask(__name__)
app.config["OUTPUT_DIR"] = Path("output")

RESOLUTION_PRESETS = {
    "270p": (480, 270),
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
}

TEXTURE_QUALITY = {
    "low": {"maximumScreenSpaceError": 24},
    "medium": {"maximumScreenSpaceError": 12},
    "high": {"maximumScreenSpaceError": 4},
    "ultra": {"maximumScreenSpaceError": 1},
}

SHOT_LIBRARY_SIZE = 10
EARTH_RADIUS_M = 6_378_137.0

tasks: dict[str, dict] = {}
stored_api_key: dict[str, str] = {}


# ─── Routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/output/<path:filepath>")
def serve_output(filepath):
    return send_from_directory(app.config["OUTPUT_DIR"], filepath)


# ─── API: Key validation ────────────────────────────────────────────────

@app.route("/api/validate-key", methods=["POST"])
def validate_key():
    data = request.get_json(force=True)
    api_key = data.get("apiKey", "").strip()
    if not api_key:
        return jsonify({"ok": False, "error": "Please enter an API key."})

    test_url = f"https://tile.googleapis.com/v1/3dtiles/root.json?key={api_key}"
    try:
        resp = requests.get(test_url, timeout=10)
        if resp.status_code == 200:
            stored_api_key["key"] = api_key
            return jsonify({"ok": True, "message": "API key valid — billing is properly connected."})
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        error_msg = body.get("error", {}).get("message", resp.text[:200])
        if resp.status_code == 403:
            return jsonify({
                "ok": False,
                "billing": False,
                "error": "API key was rejected.",
                "guide": [
                    "1. Go to Google Cloud Console → console.cloud.google.com",
                    "2. Verify billing account is linked under the Billing menu",
                    "3. APIs & Services → Library → Search 'Map Tiles API' → Enable",
                    "4. Verify that the API key includes Map Tiles API permission",
                ],
                "links": {
                    "billing": "https://console.cloud.google.com/billing",
                    "api_library": "https://console.cloud.google.com/apis/library/tile.googleapis.com",
                    "credentials": "https://console.cloud.google.com/apis/credentials",
                },
            })
        return jsonify({"ok": False, "error": f"API error ({resp.status_code}): {error_msg}"})
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Network error: {exc}"})


# ─── API: Geocode ────────────────────────────────────────────────────────

@app.route("/api/geocode", methods=["POST"])
def geocode_address():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "Please enter an address or coordinates."})

    try:
        parts = [s.strip() for s in query.split(",")]
        if len(parts) == 2:
            try:
                lat, lng = float(parts[0]), float(parts[1])
                return jsonify({"ok": True, "lat": lat, "lng": lng, "display": f"{lat}, {lng}"})
            except ValueError:
                pass

        api_key = stored_api_key.get("key", "")
        lat, lng, display = geocode(query, google_api_key=api_key)
        return jsonify({"ok": True, "lat": lat, "lng": lng, "display": display})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ─── API: Generate previews ─────────────────────────────────────────────

def _is_flyby_shot(shot_id: str) -> bool:
    return ("flyby" in shot_id) or ("flythrough" in shot_id)


def _select_shot_plans(
    lat: float,
    lng: float,
    *,
    duration_sec: int,
    num_shots: int,
    shot_mode: str = "mixed",
) -> list[ShotPlan]:
    plans = generate_shot_plans(lat, lng, duration_sec=duration_sec, num_shots=SHOT_LIBRARY_SIZE)
    mode = (shot_mode or "mixed").lower()

    if mode == "orbit":
        plans = [s for s in plans if not _is_flyby_shot(s.shot_id)]
    elif mode == "flyby":
        plans = [s for s in plans if _is_flyby_shot(s.shot_id)]

    if not plans:
        raise ValueError("No shot plans available for the selected shot type.")
    return plans[: min(num_shots, len(plans))]


def _shot_plan_by_id(lat: float, lng: float, *, duration_sec: int, shot_id: str) -> ShotPlan:
    plans = generate_shot_plans(lat, lng, duration_sec=duration_sec, num_shots=SHOT_LIBRARY_SIZE)
    for shot in plans:
        if shot.shot_id == shot_id:
            return shot
    raise ValueError(f"Unknown shot_id: {shot_id}")


def _image_hash(image_path: Path, size: int = 24) -> list[int]:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("L").resize((size, size))
        pixels = list(img.getdata())
    avg = sum(pixels) / max(len(pixels), 1)
    return [1 if px >= avg else 0 for px in pixels]


def _hash_distance(a: list[int], b: list[int]) -> int:
    if len(a) != len(b):
        raise ValueError("Hash sizes do not match.")
    return sum(1 for x, y in zip(a, b) if x != y)


def _similarity_percent(distance: int, bit_count: int) -> float:
    if bit_count <= 0:
        return 0.0
    return round((1.0 - (distance / bit_count)) * 100.0, 2)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _analyze_reference_image(image_path: Path) -> dict:
    """Lightweight image analysis to infer rough camera angle tendencies."""
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("L").resize((128, 72))
        w, h = img.size
        pixels = list(img.getdata())

    rows = [pixels[y * w:(y + 1) * w] for y in range(h)]
    cols = [pixels[x::w] for x in range(w)]

    top_mean = sum(sum(r) for r in rows[: h // 3]) / max((h // 3) * w, 1)
    bottom_mean = sum(sum(r) for r in rows[-(h // 3):]) / max((h // 3) * w, 1)
    left_mean = sum(sum(c) for c in cols[: w // 2]) / max((w // 2) * h, 1)
    right_mean = sum(sum(c) for c in cols[w // 2:]) / max((w - (w // 2)) * h, 1)

    avg = sum(pixels) / max(len(pixels), 1)
    variance = sum((p - avg) ** 2 for p in pixels) / max(len(pixels), 1)
    contrast = math.sqrt(variance) / 255.0

    vertical_bias = (bottom_mean - top_mean) / 255.0
    horizontal_bias = (right_mean - left_mean) / 255.0

    # For street-like references (sky-heavy top), reduce altitude/distance and tilt.
    tilt_shift_deg = _clamp(vertical_bias * 12.0, -14.0, 14.0)
    heading_shift_deg = _clamp(horizontal_bias * 10.0, -12.0, 12.0)
    distance_scale = _clamp(0.85 - vertical_bias * 0.65, 0.12, 1.25)
    altitude_scale = _clamp(0.9 - vertical_bias * 0.75, 0.08, 1.25)
    speed_scale = _clamp(1.0 + (0.20 - contrast) * 0.45, 0.7, 1.35)

    return {
        "tilt_shift_deg": round(tilt_shift_deg, 3),
        "heading_shift_deg": round(heading_shift_deg, 3),
        "distance_scale": round(distance_scale, 4),
        "altitude_scale": round(altitude_scale, 4),
        "speed_scale": round(speed_scale, 4),
    }


def _offsets_from_target_m(lat: float, lng: float, target_lat: float, target_lng: float) -> tuple[float, float]:
    target_lat_rad = math.radians(target_lat)
    north = math.radians(lat - target_lat) * EARTH_RADIUS_M
    east = math.radians(lng - target_lng) * EARTH_RADIUS_M * max(math.cos(target_lat_rad), 1e-8)
    return north, east


def _lat_lng_from_offsets_m(target_lat: float, target_lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    target_lat_rad = math.radians(target_lat)
    dlat = north_m / EARTH_RADIUS_M
    dlng = east_m / (EARTH_RADIUS_M * max(math.cos(target_lat_rad), 1e-8))
    return target_lat + math.degrees(dlat), target_lng + math.degrees(dlng)


def _shift_keyframes_to_reference(
    shot: ShotPlan,
    *,
    heading_shift_deg: float,
    tilt_shift_deg: float,
    distance_scale: float,
    altitude_scale: float,
    speed_scale: float,
    title_suffix: str,
) -> ShotPlan:
    transformed: list[CameraKeyframe] = []
    for k in shot.keyframes:
        north, east = _offsets_from_target_m(k.lat, k.lng, shot.target_lat, shot.target_lng)
        lat, lng = _lat_lng_from_offsets_m(
            shot.target_lat,
            shot.target_lng,
            north * distance_scale,
            east * distance_scale,
        )
        transformed.append(
            CameraKeyframe(
                t=k.t * speed_scale,
                lat=lat,
                lng=lng,
                alt_m=max(5.0, k.alt_m * altitude_scale),
                heading_deg=(k.heading_deg + heading_shift_deg) % 360.0,
                tilt_deg=_clamp(k.tilt_deg + tilt_shift_deg, 5.0, 89.0),
            )
        )

    return ShotPlan(
        shot_id=f"{shot.shot_id}_{title_suffix.lower().replace(' ', '_')}",
        title=f"{shot.title} ({title_suffix})",
        style=shot.style,
        duration_sec=max(1, int(round(shot.duration_sec * speed_scale))),
        target_lat=shot.target_lat,
        target_lng=shot.target_lng,
        keyframes=transformed,
        notes=f"{shot.notes} | Reference-adjusted: {title_suffix}",
    )


def _build_reference_variants(base_shot: ShotPlan, hints: dict) -> list[ShotPlan]:
    tilt = float(hints.get("tilt_shift_deg", 0.0))
    heading = float(hints.get("heading_shift_deg", 0.0))
    dist = float(hints.get("distance_scale", 1.0))
    alt = float(hints.get("altitude_scale", 1.0))
    speed = float(hints.get("speed_scale", 1.0))

    return [
        _shift_keyframes_to_reference(
            base_shot,
            heading_shift_deg=heading,
            tilt_shift_deg=tilt,
            distance_scale=dist,
            altitude_scale=alt,
            speed_scale=speed,
            title_suffix="Ref Match A",
        ),
        _shift_keyframes_to_reference(
            base_shot,
            heading_shift_deg=heading + 6.0,
            tilt_shift_deg=tilt - 2.0,
            distance_scale=_clamp(dist * 1.15, 0.12, 1.8),
            altitude_scale=_clamp(alt * 1.10, 0.08, 1.8),
            speed_scale=_clamp(speed * 1.10, 0.7, 1.5),
            title_suffix="Ref Match B",
        ),
        _shift_keyframes_to_reference(
            base_shot,
            heading_shift_deg=heading - 6.0,
            tilt_shift_deg=tilt + 2.0,
            distance_scale=_clamp(dist * 0.9, 0.08, 1.5),
            altitude_scale=_clamp(alt * 0.9, 0.08, 1.5),
            speed_scale=_clamp(speed * 0.92, 0.7, 1.5),
            title_suffix="Ref Match C",
        ),
    ]


def _generate_previews(task_id: str, lat: float, lng: float, num_shots: int,
                       duration_sec: int = 7, resolution: str = "270p", texture: str = "medium",
                       fps: int = 30, shot_mode: str = "mixed"):
    task = tasks[task_id]
    api_key = stored_api_key.get("key", "")
    if not api_key:
        task["status"] = "error"
        task["error"] = "API key is not set."
        return

    width, height = RESOLUTION_PRESETS.get(resolution, (480, 270))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = app.config["OUTPUT_DIR"] / f"run_{run_id}"
    task["run_id"] = f"run_{run_id}"
    task["run_dir"] = str(run_dir)

    try:
        from renderer import BatchRenderer, RenderOptions
        import shutil

        shot_plans = _select_shot_plans(
            lat, lng,
            duration_sec=duration_sec,
            num_shots=num_shots,
            shot_mode=shot_mode,
        )
        task["total"] = len(shot_plans)
        task["videos"] = []

        options = RenderOptions(
            width=width, height=height, fps=fps,
            google_api_key=api_key, headless=True,
        )

        with BatchRenderer(options) as renderer:
            renderer.boot(shot_plans[0].keyframes[0])

            for idx, shot in enumerate(shot_plans):
                task["current"] = idx + 1
                task["current_name"] = shot.title

                analysis = build_shot_analysis(shot)

                kml_path = run_dir / "kml" / f"{shot.shot_id}.kml"
                export_kml(shot, kml_path, fps=2)
                jsx_path = run_dir / "jsx" / f"{shot.shot_id}.jsx"
                export_jsx(shot, jsx_path, fps=fps, width=width, height=height)

                frame_dir = run_dir / "frames" / shot.shot_id
                rendered = renderer.render_shot(shot, frame_dir)

                video_path = run_dir / "videos" / f"{shot.shot_id}.mp4"
                _encode_with_metadata(frame_dir, video_path, shot, analysis, fps=fps)

                shutil.rmtree(frame_dir, ignore_errors=True)

                task["videos"].append({
                    "shot_id": shot.shot_id,
                    "title": shot.title,
                    "style": shot.style,
                    "video_url": f"/output/run_{run_id}/videos/{shot.shot_id}.mp4",
                    "frames": rendered,
                    "analysis": analysis,
                    "keyframes": [
                        {
                            "t": k.t, "lat": k.lat, "lng": k.lng,
                            "alt_m": k.alt_m, "heading_deg": k.heading_deg,
                            "tilt_deg": k.tilt_deg,
                        }
                        for k in shot.keyframes
                    ],
                })

        task["status"] = "done"
    except Exception as exc:
        task["status"] = "error"
        task["error"] = str(exc)


def _generate_reference_stills(task_id: str, lat: float, lng: float, reference_image: Path,
                               num_shots: int, duration_sec: int = 7, resolution: str = "270p",
                               texture: str = "medium", fps: int = 30, shot_mode: str = "mixed"):
    task = tasks[task_id]
    api_key = stored_api_key.get("key", "")
    if not api_key:
        task["status"] = "error"
        task["error"] = "API key is not set."
        return

    width, height = RESOLUTION_PRESETS.get(resolution, (480, 270))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = app.config["OUTPUT_DIR"] / f"run_{run_id}"
    task["run_id"] = f"run_{run_id}"
    task["run_dir"] = str(run_dir)

    try:
        from renderer import BatchRenderer, RenderOptions

        reference_hash = _image_hash(reference_image)
        reference_hints = _analyze_reference_image(reference_image)
        shot_plans = _select_shot_plans(
            lat, lng,
            duration_sec=duration_sec,
            num_shots=num_shots,
            shot_mode=shot_mode,
        )
        task["total"] = len(shot_plans)
        task["videos"] = []  # keep compatibility with existing pollTask progress

        options = RenderOptions(
            width=width, height=height, fps=fps,
            google_api_key=api_key, headless=True,
        )

        stills: list[dict] = []
        with BatchRenderer(options) as renderer:
            renderer.boot(shot_plans[0].keyframes[0])

            for idx, shot in enumerate(shot_plans):
                task["current"] = idx + 1
                task["current_name"] = shot.title

                still_path = run_dir / "stills" / f"{shot.shot_id}.png"
                renderer.render_still(shot, still_path, t_sec=shot.duration_sec * 0.5)

                distance = _hash_distance(reference_hash, _image_hash(still_path))
                similarity = _similarity_percent(distance, len(reference_hash))
                analysis = build_shot_analysis(shot)

                candidate = {
                    "shot_id": shot.shot_id,
                    "title": shot.title,
                    "style": shot.style,
                    "still_url": f"/output/run_{run_id}/stills/{shot.shot_id}.png",
                    "similarity": similarity,
                    "reference_hints": reference_hints,
                    "analysis": analysis,
                    "keyframes": [
                        {
                            "t": k.t, "lat": k.lat, "lng": k.lng,
                            "alt_m": k.alt_m, "heading_deg": k.heading_deg,
                            "tilt_deg": k.tilt_deg,
                        }
                        for k in shot.keyframes
                    ],
                }
                stills.append(candidate)
                task["videos"].append(candidate)

        stills.sort(key=lambda item: item["similarity"], reverse=True)
        task["reference_hints"] = reference_hints
        task["stills"] = stills
        task["videos"] = stills
        task["status"] = "done"
    except Exception as exc:
        task["status"] = "error"
        task["error"] = str(exc)


def _generate_video_from_shot(task_id: str, lat: float, lng: float, shot_id: str,
                              duration_sec: int = 7, resolution: str = "270p",
                              texture: str = "medium", fps: int = 30, codec: str = "h264",
                              reference_hints: dict | None = None):
    task = tasks[task_id]
    api_key = stored_api_key.get("key", "")
    if not api_key:
        task["status"] = "error"
        task["error"] = "API key is missing."
        return

    # Quick candidate pass is intentionally fixed to 480x270 for speed.
    width, height = RESOLUTION_PRESETS.get("270p", (480, 270))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = app.config["OUTPUT_DIR"] / f"run_{run_id}"
    task["run_id"] = f"run_{run_id}"
    task["run_dir"] = str(run_dir)

    try:
        import shutil
        from renderer import RenderOptions, render_shot_frames

        base_shot = _shot_plan_by_id(lat, lng, duration_sec=duration_sec, shot_id=shot_id)
        variants = _build_reference_variants(base_shot, reference_hints or {})
        task["total"] = len(variants)

        for idx, shot in enumerate(variants):
            task["current"] = idx + 1
            task["current_name"] = shot.title
            analysis = build_shot_analysis(shot)

            kml_path = run_dir / "kml" / f"{shot.shot_id}.kml"
            export_kml(shot, kml_path, fps=2)
            jsx_path = run_dir / "jsx" / f"{shot.shot_id}.jsx"
            export_jsx(shot, jsx_path, fps=fps, width=width, height=height)

            frame_dir = run_dir / "frames" / shot.shot_id
            rendered = render_shot_frames(
                shot=shot,
                frame_dir=frame_dir,
                options=RenderOptions(
                    width=width, height=height, fps=fps,
                    google_api_key=api_key, headless=True,
                ),
            )

            video_path = run_dir / "videos" / f"{shot.shot_id}.mp4"
            _encode_with_metadata(frame_dir, video_path, shot, analysis, fps=fps, codec=codec)
            shutil.rmtree(frame_dir, ignore_errors=True)

            task["videos"].append({
                "shot_id": shot.shot_id,
                "source_shot_id": base_shot.shot_id,
                "title": shot.title,
                "style": shot.style,
                "video_url": f"/output/run_{run_id}/videos/{shot.shot_id}.mp4",
                "frames": rendered,
                "analysis": analysis,
                "keyframes": [
                    {
                        "t": k.t, "lat": k.lat, "lng": k.lng,
                        "alt_m": k.alt_m, "heading_deg": k.heading_deg,
                        "tilt_deg": k.tilt_deg,
                    }
                    for k in shot.keyframes
                ],
            })
        task["status"] = "done"
    except Exception as exc:
        task["status"] = "error"
        task["error"] = str(exc)


def _encode_with_metadata(
    frame_dir: Path, video_path: Path,
    shot: ShotPlan, analysis: dict,
    fps: int = 24, codec: str = "h264",
):
    """Encode with metadata overlay baked at the top of the MP4."""
    video_path.parent.mkdir(parents=True, exist_ok=True)

    rec = analysis.get("recommendation", {})
    motion = analysis.get("motion", {})
    platform = rec.get("recommended_platform", "N/A")
    avg_alt = motion.get("avg_altitude_m", 0)
    avg_spd = motion.get("avg_speed_mps", 0)
    max_alt = motion.get("max_altitude_m", 0)

    overlay_text = (
        f"ALT {avg_alt:.0f}m (max {max_alt:.0f}m)  |  "
        f"SPD {avg_spd:.1f} m/s  |  "
        f"{platform.upper()}  |  "
        f"{shot.title}"
    )
    overlay_text_escaped = overlay_text.replace(":", "\\:").replace("'", "\\'")

    import subprocess
    from ffmpeg_path import get_ffmpeg
    ffmpeg = get_ffmpeg()
    pattern = str(frame_dir / "frame_%06d.png")
    enc = "libx264" if codec == "h264" else "libx265"

    cmd = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-vf", (
            f"drawtext=text='{overlay_text_escaped}'"
            f":fontsize=(h/30):fontcolor=white"
            f":borderw=1:bordercolor=black@0.6"
            f":x=(w-text_w)/2:y=(h/40)"
        ),
        "-c:v", enc,
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        str(video_path),
    ]

    subprocess.run(cmd, check=True, capture_output=True)


@app.route("/api/generate", methods=["POST"])
def start_generate():
    data = request.get_json(force=True)
    lat = data.get("lat")
    lng = data.get("lng")
    num_shots = min(max(int(data.get("numShots", 5)), 1), 10)
    duration_sec = min(max(int(data.get("duration_sec", 7)), 3), 60)
    resolution = data.get("resolution", "270p")
    texture = data.get("texture", "medium")
    fps = min(max(int(data.get("fps", 30)), 24), 60)
    shot_mode = data.get("shot_mode", "mixed")

    if lat is None or lng is None:
        return jsonify({"ok": False, "error": "Coordinates are required."})

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "running",
        "total": num_shots,
        "current": 0,
        "current_name": "",
        "videos": [],
        "lat": lat,
        "lng": lng,
    }

    thread = threading.Thread(
        target=_generate_previews,
        args=(task_id, lat, lng, num_shots),
        kwargs={
            "duration_sec": duration_sec,
            "resolution": resolution,
            "texture": texture,
            "fps": fps,
            "shot_mode": shot_mode,
        },
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "taskId": task_id})


@app.route("/api/generate-reference-stills", methods=["POST"])
def start_generate_reference_stills():
    api_key = stored_api_key.get("key", "")
    if not api_key:
        return jsonify({"ok": False, "error": "API key is not set."})

    try:
        lat = float(request.form.get("lat", "").strip())
        lng = float(request.form.get("lng", "").strip())
    except ValueError:
        return jsonify({"ok": False, "error": "Valid coordinates are required."})

    num_shots = min(max(int(request.form.get("numShots", 5)), 1), 10)
    duration_sec = min(max(int(request.form.get("duration_sec", 7)), 3), 60)
    resolution = request.form.get("resolution", "270p")
    texture = request.form.get("texture", "medium")
    fps = min(max(int(request.form.get("fps", 30)), 24), 60)
    shot_mode = request.form.get("shot_mode", "mixed")

    upload = request.files.get("reference_image")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Reference image is required."})

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    upload_dir = app.config["OUTPUT_DIR"] / "reference_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    ref_path = upload_dir / f"ref_{uuid.uuid4().hex}{suffix}"
    upload.save(ref_path)

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "running",
        "total": num_shots,
        "current": 0,
        "current_name": "",
        "videos": [],
        "stills": [],
        "lat": lat,
        "lng": lng,
        "task_type": "reference_stills",
    }

    thread = threading.Thread(
        target=_generate_reference_stills,
        args=(task_id, lat, lng, ref_path, num_shots),
        kwargs={
            "duration_sec": duration_sec,
            "resolution": resolution,
            "texture": texture,
            "fps": fps,
            "shot_mode": shot_mode,
        },
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "taskId": task_id})


@app.route("/api/generate-from-shot", methods=["POST"])
def start_generate_from_selected_shot():
    data = request.get_json(force=True)
    lat = data.get("lat")
    lng = data.get("lng")
    shot_id = data.get("shot_id", "").strip()
    duration_sec = min(max(int(data.get("duration_sec", 7)), 3), 60)
    resolution = data.get("resolution", "270p")
    texture = data.get("texture", "medium")
    fps = min(max(int(data.get("fps", 30)), 24), 60)
    codec = data.get("codec", "h264")
    reference_hints = data.get("reference_hints") or {}

    if lat is None or lng is None:
        return jsonify({"ok": False, "error": "Coordinates are required."})
    if not shot_id:
        return jsonify({"ok": False, "error": "shot_id is required."})

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "status": "running",
        "total": 3,
        "current": 0,
        "current_name": "",
        "videos": [],
        "lat": lat,
        "lng": lng,
        "task_type": "single_shot_video",
    }

    thread = threading.Thread(
        target=_generate_video_from_shot,
        args=(task_id, lat, lng, shot_id),
        kwargs={
            "duration_sec": duration_sec,
            "resolution": resolution,
            "texture": texture,
            "fps": fps,
            "codec": codec,
            "reference_hints": reference_hints,
        },
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "taskId": task_id})


@app.route("/api/task/<task_id>")
def get_task_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "Task not found."})
    return jsonify({"ok": True, **task})


# ─── API: Regenerate single shot with custom params ─────────────────────

@app.route("/api/regenerate", methods=["POST"])
def regenerate_shot():
    data = request.get_json(force=True)
    api_key = stored_api_key.get("key", "")
    if not api_key:
        return jsonify({"ok": False, "error": "API key is missing."})

    lat = data["lat"]
    lng = data["lng"]
    alt_m = data.get("alt_m", 500)
    heading_deg = data.get("heading_deg", 0)
    tilt_deg = data.get("tilt_deg", 30)
    speed_factor = data.get("speed_factor", 1.0)
    orbit_radius_m = data.get("orbit_radius_m", 850)
    sweep_deg = data.get("sweep_deg", 45)
    resolution = data.get("resolution", "480p")
    texture = data.get("texture", "medium")
    duration_sec = data.get("duration_sec", 7)
    codec = data.get("codec", "h264")

    width, height = RESOLUTION_PRESETS.get(resolution, (854, 480))

    from camera_path import _orbit_keyframes
    keyframes = _orbit_keyframes(
        lat, lng, duration_sec,
        radius_start_m=orbit_radius_m,
        radius_end_m=orbit_radius_m,
        alt_start_m=alt_m,
        alt_end_m=alt_m,
        azimuth_start_deg=heading_deg,
        sweep_deg=sweep_deg / max(speed_factor, 0.1),
        tilt_offset_deg=tilt_deg,
    )

    shot = ShotPlan(
        shot_id="custom_regen",
        title="Custom Regeneration",
        style="helicopter",
        duration_sec=duration_sec,
        target_lat=lat,
        target_lng=lng,
        keyframes=keyframes,
        notes="User customized shot",
    )

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "running", "total": 1, "current": 0, "current_name": "", "videos": []}

    def _run():
        task = tasks[task_id]
        try:
            task["current"] = 1
            task["current_name"] = "Custom Regeneration"

            analysis = build_shot_analysis(shot)
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = app.config["OUTPUT_DIR"] / f"run_{run_id}"
            task["run_id"] = f"run_{run_id}"

            from renderer import RenderOptions, render_shot_frames
            frame_dir = run_dir / "frames" / "custom_regen"
            rendered = render_shot_frames(
                shot=shot,
                frame_dir=frame_dir,
                options=RenderOptions(
                    width=width, height=height, fps=30,
                    google_api_key=api_key, headless=True,
                ),
            )

            video_path = run_dir / "videos" / "custom_regen.mp4"
            _encode_with_metadata(frame_dir, video_path, shot, analysis, fps=30, codec=codec)

            import shutil
            shutil.rmtree(frame_dir, ignore_errors=True)

            kml_path = run_dir / "kml" / "custom_regen.kml"
            export_kml(shot, kml_path, fps=2)
            jsx_path = run_dir / "jsx" / "custom_regen.jsx"
            export_jsx(shot, jsx_path, fps=30, width=width, height=height)

            task["videos"].append({
                "shot_id": "custom_regen",
                "title": "Custom Regeneration",
                "style": "helicopter",
                "video_url": f"/output/run_{run_id}/videos/custom_regen.mp4",
                "frames": rendered,
                "analysis": analysis,
            })
            task["status"] = "done"
        except Exception as exc:
            task["status"] = "error"
            task["error"] = str(exc)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "taskId": task_id})


if __name__ == "__main__":
    app.config["OUTPUT_DIR"].mkdir(exist_ok=True)
    app.run(host="127.0.0.1", port=5100, debug=False)
