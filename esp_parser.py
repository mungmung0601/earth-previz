from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean

from models import CameraKeyframe, ShotPlan

EARTH_A = 6_378_137.0
EARTH_F = 1.0 / 298.257223563
EARTH_B = EARTH_A * (1.0 - EARTH_F)
EARTH_E2 = 2.0 * EARTH_F - EARTH_F * EARTH_F


def _ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    lng_rad = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)
    lat_rad = math.atan2(z, p * (1.0 - EARTH_E2))

    for _ in range(12):
        sin_lat = math.sin(lat_rad)
        n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * sin_lat * sin_lat)
        lat_rad = math.atan2(z + EARTH_E2 * n * sin_lat, p)

    sin_lat = math.sin(lat_rad)
    n = EARTH_A / math.sqrt(1.0 - EARTH_E2 * sin_lat * sin_lat)

    cos_lat = math.cos(lat_rad)
    if abs(cos_lat) > 1e-10:
        alt = p / cos_lat - n
    else:
        alt = abs(z) - EARTH_B

    return math.degrees(lat_rad), math.degrees(lng_rad), alt


def _ecef_magnitude(x: float, y: float, z: float) -> float:
    return math.sqrt(x * x + y * y + z * z)


def _bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    y = math.sin(dlng) * math.cos(lat2_r)
    x = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlng)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _ground_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lng1_r = math.radians(lat1), math.radians(lng1)
    lat2_r, lng2_r = math.radians(lat2), math.radians(lng2)
    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return 2.0 * EARTH_A * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _look_at_tilt_deg(
    cam_lat: float, cam_lng: float, cam_alt: float,
    target_lat: float, target_lng: float,
) -> float:
    gd = _ground_distance_m(cam_lat, cam_lng, target_lat, target_lng)
    return math.degrees(math.atan2(cam_alt, max(gd, 1.0)))


def _extract_trackpoints(data: dict) -> list[dict]:
    raw = data.get("trackPoints", [])
    coords: list[dict] = []
    for tp in raw:
        try:
            attrs = tp["coordinate"]["position"]["attributes"]
            rel_0 = float(attrs[0]["value"]["relative"])
            rel_1 = float(attrs[1]["value"]["relative"])
            rel_2 = float(attrs[2]["value"]["relative"])
            lng = 360.0 * rel_0 - 180.0
            lat = 179.9998 * rel_1 - 89.9999
            alt = 65_117_481.0 * rel_2 + 1.0
            coords.append({"name": tp.get("name", ""), "lat": lat, "lng": lng, "alt": alt})
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return coords


def _detect_ecef_scale(sample_pos: dict) -> float | None:
    x, y, z = float(sample_pos["x"]), float(sample_pos["y"]), float(sample_pos["z"])
    mag = _ecef_magnitude(x, y, z)
    if 5_000_000 < mag < 8_000_000:
        return 1.0
    mag100 = _ecef_magnitude(x * 100, y * 100, z * 100)
    if 5_000_000 < mag100 < 8_000_000:
        return 100.0
    return None


def _subsample_indices(total: int, max_keyframes: int) -> list[int]:
    if total <= max_keyframes:
        return list(range(total))
    step = (total - 1) / (max_keyframes - 1)
    return [round(step * i) for i in range(max_keyframes)]


def parse_esp(
    filepath: str | Path,
    *,
    fps: int = 24,
    max_keyframes: int = 25,
    target_lat: float | None = None,
    target_lng: float | None = None,
) -> tuple[ShotPlan, dict]:
    """Parse an ESP/JSON file and return a ShotPlan with metadata."""
    filepath = Path(filepath)
    data = json.loads(filepath.read_text(encoding="utf-8"))

    camera_frames = data.get("cameraFrames", [])
    if not camera_frames:
        raise ValueError(f"No cameraFrames found in: {filepath}")

    trackpoints = _extract_trackpoints(data)

    if target_lat is None or target_lng is None:
        if trackpoints:
            target_lat = trackpoints[0]["lat"]
            target_lng = trackpoints[0]["lng"]
        else:
            target_lat = 0.0
            target_lng = 0.0

    scale = _detect_ecef_scale(camera_frames[0]["position"])
    if scale is None:
        raise ValueError(
            "Unable to interpret cameraFrames position coordinates as ECEF. "
            "Please verify this is an Earth Studio 3D Camera Export JSON file."
        )

    all_geodetic: list[tuple[float, float, float]] = []
    for frame in camera_frames:
        pos = frame["position"]
        x = float(pos["x"]) * scale
        y = float(pos["y"]) * scale
        z = float(pos["z"]) * scale
        all_geodetic.append(_ecef_to_geodetic(x, y, z))

    if target_lat == 0.0 and target_lng == 0.0 and not trackpoints:
        lats = [g[0] for g in all_geodetic]
        lngs = [g[1] for g in all_geodetic]
        target_lat = mean(lats)
        target_lng = mean(lngs)

    total_frames = len(all_geodetic)
    duration_sec = total_frames / max(fps, 1)

    indices = _subsample_indices(total_frames, max_keyframes)
    keyframes: list[CameraKeyframe] = []

    for idx in indices:
        lat, lng, alt = all_geodetic[idx]
        t = idx / max(fps, 1)
        heading = _bearing_deg(lat, lng, target_lat, target_lng)
        tilt = _look_at_tilt_deg(lat, lng, alt, target_lat, target_lng)

        keyframes.append(
            CameraKeyframe(
                t=t,
                lat=lat,
                lng=lng,
                alt_m=alt,
                heading_deg=heading,
                tilt_deg=tilt,
            )
        )

    shot = ShotPlan(
        shot_id=filepath.stem,
        title=f"ESP Import: {filepath.stem}",
        style="imported",
        duration_sec=round(duration_sec),
        target_lat=target_lat,
        target_lng=target_lng,
        keyframes=keyframes,
        notes=f"Camera path imported from Earth Studio project. Original frame count: {total_frames}",
    )

    meta = {
        "source_file": str(filepath),
        "total_source_frames": total_frames,
        "source_fps": fps,
        "duration_sec": round(duration_sec, 2),
        "keyframe_count": len(keyframes),
        "trackpoints": trackpoints,
        "target_lat": target_lat,
        "target_lng": target_lng,
    }

    return shot, meta
