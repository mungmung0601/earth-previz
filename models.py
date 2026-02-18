from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CameraKeyframe:
    t: float
    lat: float
    lng: float
    alt_m: float
    heading_deg: float
    tilt_deg: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShotPlan:
    shot_id: str
    title: str
    style: str
    duration_sec: int
    target_lat: float
    target_lng: float
    keyframes: list[CameraKeyframe]
    notes: str

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "title": self.title,
            "style": self.style,
            "duration_sec": self.duration_sec,
            "target_lat": self.target_lat,
            "target_lng": self.target_lng,
            "notes": self.notes,
            "keyframes": [k.to_dict() for k in self.keyframes],
        }
