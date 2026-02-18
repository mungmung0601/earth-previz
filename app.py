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

from camera_path import generate_shot_plans
from encoder import encode_frames_to_mp4
from geocoder import geocode
from jsx_exporter import export_jsx
from kml_exporter import export_kml
from models import ShotPlan
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
        return jsonify({"ok": False, "error": "API 키를 입력하세요."})

    test_url = f"https://tile.googleapis.com/v1/3dtiles/root.json?key={api_key}"
    try:
        resp = requests.get(test_url, timeout=10)
        if resp.status_code == 200:
            stored_api_key["key"] = api_key
            return jsonify({"ok": True, "message": "API 키 유효 — 빌링이 정상적으로 연결되어 있습니다."})
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        error_msg = body.get("error", {}).get("message", resp.text[:200])
        if resp.status_code == 403:
            return jsonify({
                "ok": False,
                "billing": False,
                "error": "API 키가 거부되었습니다.",
                "guide": [
                    "1. Google Cloud Console 접속 → console.cloud.google.com",
                    "2. 결제(Billing) 메뉴에서 결제 계정 연결 확인",
                    "3. API 및 서비스 → 라이브러리 → 'Map Tiles API' 검색 → 사용 설정",
                    "4. API 키에 Map Tiles API 권한이 포함되어 있는지 확인",
                ],
                "links": {
                    "billing": "https://console.cloud.google.com/billing",
                    "api_library": "https://console.cloud.google.com/apis/library/tile.googleapis.com",
                    "credentials": "https://console.cloud.google.com/apis/credentials",
                },
            })
        return jsonify({"ok": False, "error": f"API 오류 ({resp.status_code}): {error_msg}"})
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"네트워크 오류: {exc}"})


# ─── API: Geocode ────────────────────────────────────────────────────────

@app.route("/api/geocode", methods=["POST"])
def geocode_address():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"ok": False, "error": "주소 또는 좌표를 입력하세요."})

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

def _generate_previews(task_id: str, lat: float, lng: float, num_shots: int,
                       duration_sec: int = 7, resolution: str = "270p", texture: str = "medium"):
    task = tasks[task_id]
    api_key = stored_api_key.get("key", "")
    if not api_key:
        task["status"] = "error"
        task["error"] = "API 키가 설정되지 않았습니다."
        return

    width, height = RESOLUTION_PRESETS.get(resolution, (480, 270))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = app.config["OUTPUT_DIR"] / f"run_{run_id}"
    task["run_id"] = f"run_{run_id}"
    task["run_dir"] = str(run_dir)

    try:
        shot_plans = generate_shot_plans(lat, lng, duration_sec=duration_sec, num_shots=num_shots)
        task["total"] = len(shot_plans)
        task["videos"] = []

        for idx, shot in enumerate(shot_plans):
            task["current"] = idx + 1
            task["current_name"] = shot.title

            analysis = build_shot_analysis(shot)

            kml_path = run_dir / "kml" / f"{shot.shot_id}.kml"
            export_kml(shot, kml_path, fps=2)
            jsx_path = run_dir / "jsx" / f"{shot.shot_id}.jsx"
            export_jsx(shot, jsx_path, fps=24, width=width, height=height)

            from renderer import RenderOptions, render_shot_frames

            frame_dir = run_dir / "frames" / shot.shot_id
            rendered = render_shot_frames(
                shot=shot,
                frame_dir=frame_dir,
                options=RenderOptions(
                    width=width, height=height, fps=24,
                    google_api_key=api_key, headless=True,
                ),
            )

            video_path = run_dir / "videos" / f"{shot.shot_id}.mp4"
            _encode_with_metadata(frame_dir, video_path, shot, analysis, fps=24)

            import shutil
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


def _encode_with_metadata(
    frame_dir: Path, video_path: Path,
    shot: ShotPlan, analysis: dict,
    fps: int = 24, codec: str = "h264",
):
    """MP4 상단에 메타데이터 오버레이를 베이크하여 인코딩."""
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
    pattern = str(frame_dir / "frame_%06d.png")
    enc = "libx264" if codec == "h264" else "libx265"

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", pattern,
        "-vf", (
            f"drawtext=text='{overlay_text_escaped}'"
            f":fontsize=14:fontcolor=white"
            f":borderw=2:bordercolor=black"
            f":x=(w-text_w)/2:y=12"
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

    if lat is None or lng is None:
        return jsonify({"ok": False, "error": "좌표가 필요합니다."})

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
        kwargs={"duration_sec": duration_sec, "resolution": resolution, "texture": texture},
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "taskId": task_id})


@app.route("/api/task/<task_id>")
def get_task_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"ok": False, "error": "태스크를 찾을 수 없습니다."})
    return jsonify({"ok": True, **task})


# ─── API: Regenerate single shot with custom params ─────────────────────

@app.route("/api/regenerate", methods=["POST"])
def regenerate_shot():
    data = request.get_json(force=True)
    api_key = stored_api_key.get("key", "")
    if not api_key:
        return jsonify({"ok": False, "error": "API 키가 없습니다."})

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
        tilt_offset_deg=tilt_deg - 30,
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
                    width=width, height=height, fps=24,
                    google_api_key=api_key, headless=True,
                ),
            )

            video_path = run_dir / "videos" / "custom_regen.mp4"
            _encode_with_metadata(frame_dir, video_path, shot, analysis, fps=24, codec=codec)

            import shutil
            shutil.rmtree(frame_dir, ignore_errors=True)

            kml_path = run_dir / "kml" / "custom_regen.kml"
            export_kml(shot, kml_path, fps=2)
            jsx_path = run_dir / "jsx" / "custom_regen.jsx"
            export_jsx(shot, jsx_path, fps=24, width=width, height=height)

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
