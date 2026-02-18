from __future__ import annotations

import json
import math
from pathlib import Path

from models import CameraKeyframe, ShotPlan

EARTH_A = 6_378_137.0
EARTH_F = 1.0 / 298.257223563
EARTH_E2 = 2.0 * EARTH_F - EARTH_F * EARTH_F


def _geodetic_to_ecef(lat_deg: float, lng_deg: float, alt_m: float) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lng = math.radians(lng_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lng = math.sin(lng)
    cos_lng = math.cos(lng)
    n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * sin_lat * sin_lat)
    x = (n + alt_m) * cos_lat * cos_lng
    y = (n + alt_m) * cos_lat * sin_lng
    z = (n * (1.0 - EARTH_E2) + alt_m) * sin_lat
    return x, y, z


def _lat_to_relative(lat_deg: float) -> float:
    return (lat_deg + 89.9999) / 179.9998


def _lng_to_relative(lng_deg: float) -> float:
    return (lng_deg + 180.0) / 360.0


def _alt_to_relative(alt_m: float) -> float:
    return max((alt_m - 1.0) / 65_117_481.0, 0.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_angle(a: float, b: float, t: float) -> float:
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return (a + delta * t + 360.0) % 360.0


def _interpolate_keyframes(keyframes: list[CameraKeyframe], fps: int) -> list[dict]:
    """Interpolate keyframes to generate per-frame camera data."""
    if not keyframes:
        return []

    duration = keyframes[-1].t
    total_frames = max(int(duration * fps), 1)
    frames: list[dict] = []

    ki = 0
    for fi in range(total_frames):
        t = fi / max(fps, 1)

        while ki < len(keyframes) - 2 and keyframes[ki + 1].t < t:
            ki += 1

        a = keyframes[ki]
        b = keyframes[min(ki + 1, len(keyframes) - 1)]
        span = max(b.t - a.t, 1e-8)
        p = max(0.0, min(1.0, (t - a.t) / span))

        lat = _lerp(a.lat, b.lat, p)
        lng = _lerp(a.lng, b.lng, p)
        alt = _lerp(a.alt_m, b.alt_m, p)
        heading = _lerp_angle(a.heading_deg, b.heading_deg, p)
        tilt = _lerp(a.tilt_deg, b.tilt_deg, p)

        ex, ey, ez = _geodetic_to_ecef(lat, lng, alt)

        frames.append({
            "position": {"x": ex, "y": ey, "z": ez},
            "rotation": {"x": -tilt, "y": -heading, "z": 0.0},
        })

    return frames


def export_esp(
    shot: ShotPlan,
    output_path: Path,
    *,
    fps: int = 24,
) -> None:
    """Export ShotPlan as an Earth Studio compatible ESP (JSON) file."""
    camera_frames = _interpolate_keyframes(shot.keyframes, fps)

    target_ecef = _geodetic_to_ecef(shot.target_lat, shot.target_lng, 0.0)
    trackpoint = {
        "name": "target",
        "position": {"x": target_ecef[0], "y": target_ecef[1], "z": target_ecef[2]},
        "coordinate": {
            "position": {
                "attributes": [
                    {"value": {"relative": _lng_to_relative(shot.target_lng)}},
                    {"value": {"relative": _lat_to_relative(shot.target_lat)}},
                    {"value": {"relative": _alt_to_relative(0.0)}},
                ]
            }
        },
    }

    esp_data = {
        "type": "aerial_cinematic_bot_export",
        "version": "1.0",
        "shot_id": shot.shot_id,
        "title": shot.title,
        "style": shot.style,
        "notes": shot.notes,
        "fps": fps,
        "duration_sec": shot.duration_sec,
        "target": {
            "lat": shot.target_lat,
            "lng": shot.target_lng,
        },
        "trackPoints": [trackpoint],
        "cameraFrames": camera_frames,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(esp_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
