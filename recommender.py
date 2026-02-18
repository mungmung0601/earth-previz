from __future__ import annotations

import math
from statistics import mean

from models import CameraKeyframe, ShotPlan

EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lng1_r = math.radians(lat1), math.radians(lng1)
    lat2_r, lng2_r = math.radians(lat2), math.radians(lng2)
    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def segment_metrics(keyframes: list[CameraKeyframe]) -> list[dict]:
    segments: list[dict] = []
    for i in range(len(keyframes) - 1):
        a = keyframes[i]
        b = keyframes[i + 1]
        dt = max(b.t - a.t, 1e-6)
        ground_dist = _haversine_m(a.lat, a.lng, b.lat, b.lng)
        vertical_dist = abs(b.alt_m - a.alt_m)
        dist_3d = math.sqrt(ground_dist**2 + vertical_dist**2)
        speed = dist_3d / dt
        segments.append(
            {
                "segment_index": i,
                "t_start": a.t,
                "t_end": b.t,
                "ground_distance_m": ground_dist,
                "vertical_distance_m": vertical_dist,
                "distance_3d_m": dist_3d,
                "speed_mps": speed,
            }
        )
    return segments


def summarize_motion(shot: ShotPlan) -> dict:
    segments = segment_metrics(shot.keyframes)
    speeds = [s["speed_mps"] for s in segments] or [0.0]
    altitudes = [k.alt_m for k in shot.keyframes]
    radii = [_haversine_m(k.lat, k.lng, shot.target_lat, shot.target_lng) for k in shot.keyframes]

    return {
        "duration_sec": shot.duration_sec,
        "keyframe_count": len(shot.keyframes),
        "segment_count": len(segments),
        "avg_speed_mps": mean(speeds),
        "min_speed_mps": min(speeds),
        "max_speed_mps": max(speeds),
        "avg_altitude_m": mean(altitudes),
        "min_altitude_m": min(altitudes),
        "max_altitude_m": max(altitudes),
        "avg_radius_m": mean(radii),
        "min_radius_m": min(radii),
        "max_radius_m": max(radii),
    }


def recommend_platform(motion: dict) -> dict:
    drone_score = 0
    heli_score = 0
    reasons: list[str] = []

    max_alt = motion["max_altitude_m"]
    max_speed = motion["max_speed_mps"]
    avg_radius = motion["avg_radius_m"]

    if max_alt <= 120:
        drone_score += 3
        reasons.append("최대 고도가 낮아 드론 운용 범위에 적합합니다.")
    elif max_alt <= 220:
        drone_score += 1
        heli_score += 1
        reasons.append("중간 고도라 드론/헬기 모두 가능성이 있습니다.")
    else:
        heli_score += 3
        reasons.append("최대 고도가 높아 헬리콥터가 안정적입니다.")

    if max_speed <= 10:
        drone_score += 3
        reasons.append("최대 속도가 낮아 드론 워킹샷에 유리합니다.")
    elif max_speed <= 18:
        drone_score += 1
        heli_score += 1
        reasons.append("속도 요구가 중간 수준입니다.")
    else:
        heli_score += 3
        reasons.append("속도 요구가 커 헬리콥터가 더 자연스럽습니다.")

    if avg_radius <= 500:
        drone_score += 2
        reasons.append("평균 반경이 좁아 드론 근접 무빙에 적합합니다.")
    elif avg_radius <= 1_000:
        drone_score += 1
        heli_score += 1
        reasons.append("평균 반경이 중간 규모입니다.")
    else:
        heli_score += 2
        reasons.append("평균 반경이 넓어 헬리콥터가 유리합니다.")

    if heli_score > drone_score:
        platform = "helicopter"
    elif drone_score > heli_score:
        platform = "drone"
    else:
        platform = "hybrid"

    total = max(drone_score + heli_score, 1)
    confidence = abs(drone_score - heli_score) / total

    return {
        "recommended_platform": platform,
        "confidence": round(confidence, 3),
        "drone_score": drone_score,
        "helicopter_score": heli_score,
        "reasons": reasons,
    }


def build_shot_analysis(shot: ShotPlan) -> dict:
    motion = summarize_motion(shot)
    recommendation = recommend_platform(motion)

    return {
        "shot": shot.to_dict(),
        "motion": motion,
        "recommendation": recommendation,
        "segments": segment_metrics(shot.keyframes),
    }
