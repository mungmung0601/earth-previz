from __future__ import annotations

import math
from typing import Callable

from models import CameraKeyframe, ShotPlan

EARTH_RADIUS_M = 6_378_137.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _offset_lat_lng(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    d_lat = north_m / EARTH_RADIUS_M
    d_lng = east_m / (EARTH_RADIUS_M * max(math.cos(lat_rad), 1e-8))
    return lat + math.degrees(d_lat), lng + math.degrees(d_lng)


def _bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    y = math.sin(dlng) * math.cos(lat2_r)
    x = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlng)
    brng = math.degrees(math.atan2(y, x))
    return (brng + 360.0) % 360.0


def _ground_distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lng1_r = math.radians(lat1), math.radians(lng1)
    lat2_r, lng2_r = math.radians(lat2), math.radians(lng2)
    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _look_at_tilt_deg(
    cam_lat: float, cam_lng: float, cam_alt: float,
    target_lat: float, target_lng: float,
) -> float:
    """Tilt angle to place the target coordinates precisely at the center of the frame from the camera."""
    ground_dist = _ground_distance_m(cam_lat, cam_lng, target_lat, target_lng)
    return math.degrees(math.atan2(cam_alt, max(ground_dist, 1.0)))


_ORBIT_TILT_OFFSET_DEG = -5.0

_MIN_TILT_DEG = 5.0
_MAX_TILT_DEG = 89.0


def _orbit_keyframes(
    target_lat: float,
    target_lng: float,
    duration_sec: int,
    *,
    radius_start_m: float,
    radius_end_m: float,
    alt_start_m: float,
    alt_end_m: float,
    azimuth_start_deg: float,
    sweep_deg: float,
    tilt_offset_deg: float = _ORBIT_TILT_OFFSET_DEG,
    samples: int = 13,
) -> list[CameraKeyframe]:
    keyframes: list[CameraKeyframe] = []
    for i in range(samples):
        p = i / (samples - 1)
        radius = _lerp(radius_start_m, radius_end_m, p)
        azimuth_deg = azimuth_start_deg + sweep_deg * p
        azimuth = math.radians(azimuth_deg)
        north = radius * math.cos(azimuth)
        east = radius * math.sin(azimuth)

        lat, lng = _offset_lat_lng(target_lat, target_lng, north, east)
        alt = _lerp(alt_start_m, alt_end_m, p)
        heading_deg = _bearing_deg(lat, lng, target_lat, target_lng)
        raw_tilt = _look_at_tilt_deg(lat, lng, alt, target_lat, target_lng) + tilt_offset_deg
        tilt_deg = max(_MIN_TILT_DEG, min(_MAX_TILT_DEG, raw_tilt))

        keyframes.append(
            CameraKeyframe(
                t=duration_sec * p,
                lat=lat,
                lng=lng,
                alt_m=alt,
                heading_deg=heading_deg,
                tilt_deg=tilt_deg,
            )
        )
    return keyframes


def _dolly_keyframes(
    target_lat: float,
    target_lng: float,
    duration_sec: int,
    *,
    approach_azimuth_deg: float,
    distance_start_m: float,
    distance_end_m: float,
    alt_start_m: float,
    alt_end_m: float,
    lateral_offset_start_m: float = 0.0,
    lateral_offset_end_m: float = 0.0,
    look_forward: bool = False,
    forward_tilt_deg: float = 78.0,
    samples: int = 13,
) -> list[CameraKeyframe]:
    forward = math.radians(approach_azimuth_deg)
    right = math.radians(approach_azimuth_deg + 90.0)

    positions: list[tuple[float, float, float]] = []
    for i in range(samples):
        p = i / (samples - 1)
        dist = _lerp(distance_start_m, distance_end_m, p)
        lateral = _lerp(lateral_offset_start_m, lateral_offset_end_m, p)

        north = dist * math.cos(forward) + lateral * math.cos(right)
        east = dist * math.sin(forward) + lateral * math.sin(right)
        lat, lng = _offset_lat_lng(target_lat, target_lng, north, east)
        alt = _lerp(alt_start_m, alt_end_m, p)
        positions.append((lat, lng, alt))

    keyframes: list[CameraKeyframe] = []
    for i, (lat, lng, alt) in enumerate(positions):
        p = i / (samples - 1)

        if look_forward:
            if i < len(positions) - 1:
                nxt_lat, nxt_lng, _ = positions[i + 1]
            else:
                prv_lat, prv_lng, _ = positions[i - 1]
                nxt_lat = lat + (lat - prv_lat)
                nxt_lng = lng + (lng - prv_lng)
            heading_deg = _bearing_deg(lat, lng, nxt_lat, nxt_lng)
            tilt_deg = max(_MIN_TILT_DEG, min(_MAX_TILT_DEG, forward_tilt_deg))
        else:
            heading_deg = _bearing_deg(lat, lng, target_lat, target_lng)
            raw_tilt = _look_at_tilt_deg(lat, lng, alt, target_lat, target_lng)
            tilt_deg = max(_MIN_TILT_DEG, min(_MAX_TILT_DEG, raw_tilt))

        keyframes.append(
            CameraKeyframe(
                t=duration_sec * p,
                lat=lat,
                lng=lng,
                alt_m=alt,
                heading_deg=heading_deg,
                tilt_deg=tilt_deg,
            )
        )
    return keyframes


def _figure_eight_keyframes(
    target_lat: float,
    target_lng: float,
    duration_sec: int,
    *,
    radius_m: float,
    alt_start_m: float,
    alt_end_m: float,
    loops: float = 1.0,
    samples: int = 17,
) -> list[CameraKeyframe]:
    keyframes: list[CameraKeyframe] = []
    for i in range(samples):
        p = i / (samples - 1)
        theta = 2.0 * math.pi * loops * p
        east = radius_m * math.sin(theta)
        north = radius_m * math.sin(theta) * math.cos(theta)
        lat, lng = _offset_lat_lng(target_lat, target_lng, north, east)
        alt = _lerp(alt_start_m, alt_end_m, p)
        heading_deg = _bearing_deg(lat, lng, target_lat, target_lng)
        raw_tilt = _look_at_tilt_deg(lat, lng, alt, target_lat, target_lng)
        tilt_deg = max(_MIN_TILT_DEG, min(_MAX_TILT_DEG, raw_tilt))

        keyframes.append(
            CameraKeyframe(
                t=duration_sec * p,
                lat=lat,
                lng=lng,
                alt_m=alt,
                heading_deg=heading_deg,
                tilt_deg=tilt_deg,
            )
        )
    return keyframes


def generate_shot_plans(
    target_lat: float,
    target_lng: float,
    duration_sec: int = 300,
    num_shots: int = 10,
) -> list[ShotPlan]:
    builders: list[tuple[str, str, str, str, Callable[[], list[CameraKeyframe]]]] = [
        # 1: orbit
        (
            "aerial_slow_orbit_close",
            "Aerial Slow Orbit (Close)",
            "helicopter",
            "Mid-altitude close orbit. Medium distance to avoid building clipping.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=850,
                radius_end_m=850,
                alt_start_m=475,
                alt_end_m=475,
                azimuth_start_deg=0,
                sweep_deg=15,
            ),
        ),
        # 2: flyby
        (
            "aerial_flyby_north",
            "Aerial Flyby (North→South)",
            "helicopter",
            "Straight flyby. Aerial shot passing alongside the building (forward-looking).",
            lambda: _dolly_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                approach_azimuth_deg=0,
                distance_start_m=400,
                distance_end_m=400,
                alt_start_m=500,
                alt_end_m=500,
                lateral_offset_start_m=-50,
                lateral_offset_end_m=50,
                look_forward=True,
                forward_tilt_deg=78.0,
            ),
        ),
        # 3: extreme slow
        (
            "aerial_extreme_slow",
            "Aerial Extreme Slow Orbit",
            "helicopter",
            "Extremely slow orbit. Barely perceptible movement, ideal for screensaver loops.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=1_000,
                radius_end_m=1_000,
                alt_start_m=550,
                alt_end_m=550,
                azimuth_start_deg=270,
                sweep_deg=5,
            ),
        ),
        # 4: flythrough
        (
            "aerial_flythrough_east",
            "Aerial Flythrough (East→West)",
            "helicopter",
            "Flythrough. Passes laterally with the building centered on screen.",
            lambda: _dolly_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                approach_azimuth_deg=90,
                distance_start_m=350,
                distance_end_m=350,
                alt_start_m=600,
                alt_end_m=600,
                lateral_offset_start_m=-40,
                lateral_offset_end_m=40,
                look_forward=False,
            ),
        ),
        # 5: wide orbit
        (
            "aerial_slow_orbit_wide",
            "Aerial Slow Orbit (Wide)",
            "helicopter",
            "High-altitude wide orbit. City-scale screensaver.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=1_500,
                radius_end_m=1_500,
                alt_start_m=750,
                alt_end_m=750,
                azimuth_start_deg=45,
                sweep_deg=13,
            ),
        ),
        # 6: diagonal flyby
        (
            "aerial_flyby_diagonal",
            "Aerial Flyby (Diagonal)",
            "helicopter",
            "Diagonal flyby. Passes diagonally alongside the building (forward-looking).",
            lambda: _dolly_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                approach_azimuth_deg=45,
                distance_start_m=450,
                distance_end_m=450,
                alt_start_m=700,
                alt_end_m=700,
                lateral_offset_start_m=-47,
                lateral_offset_end_m=47,
                look_forward=True,
                forward_tilt_deg=78.0,
            ),
        ),
        # 7: descent
        (
            "aerial_slow_descent",
            "Aerial Slow Descent Orbit",
            "helicopter",
            "Orbit with slow altitude descent. Creates a sense of gradually approaching.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=1_500,
                radius_end_m=800,
                alt_start_m=1_000,
                alt_end_m=450,
                azimuth_start_deg=90,
                sweep_deg=17,
            ),
        ),
        # 8: grand panorama
        (
            "aerial_grand_panorama",
            "Aerial Grand Panorama",
            "helicopter",
            "Ultra-high altitude large arc. Panoramic view of the entire terrain.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=2_500,
                radius_end_m=2_500,
                alt_start_m=1_400,
                alt_end_m=1_400,
                azimuth_start_deg=270,
                sweep_deg=10,
            ),
        ),
        # 9: ascent
        (
            "aerial_slow_ascent",
            "Aerial Slow Ascent Orbit",
            "helicopter",
            "Orbit with slow altitude ascent. Creates a sense of expanding scale.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=600,
                radius_end_m=1_600,
                alt_start_m=400,
                alt_end_m=1_100,
                azimuth_start_deg=200,
                sweep_deg=17,
            ),
        ),
        # 10: ultra high
        (
            "aerial_ultra_high",
            "Aerial Ultra High Overview",
            "helicopter",
            "Ultra-high altitude ultra-wide orbit. Screensaver overlooking the entire area.",
            lambda: _orbit_keyframes(
                target_lat,
                target_lng,
                duration_sec,
                radius_start_m=3_000,
                radius_end_m=3_000,
                alt_start_m=2_000,
                alt_end_m=2_000,
                azimuth_start_deg=120,
                sweep_deg=8,
            ),
        ),
    ]

    if num_shots < 1:
        raise ValueError("num_shots must be at least 1")

    plans: list[ShotPlan] = []
    for shot_id, title, style, notes, builder in builders[: min(num_shots, len(builders))]:
        plans.append(
            ShotPlan(
                shot_id=shot_id,
                title=title,
                style=style,
                duration_sec=duration_sec,
                target_lat=target_lat,
                target_lng=target_lng,
                keyframes=builder(),
                notes=notes,
            )
        )
    return plans
