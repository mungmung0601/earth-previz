from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from models import CameraKeyframe, ShotPlan


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_angle(a: float, b: float, t: float) -> float:
    delta = ((b - a + 180.0) % 360.0) - 180.0
    return (a + delta * t + 360.0) % 360.0


def _interpolate_for_tour(
    keyframes: list[CameraKeyframe],
    fps: int,
) -> list[dict]:
    if not keyframes or len(keyframes) < 2:
        return [
            {
                "lat": k.lat,
                "lng": k.lng,
                "alt": k.alt_m,
                "heading": k.heading_deg,
                "tilt": k.tilt_deg,
                "duration": 0.0,
            }
            for k in keyframes
        ]

    points: list[dict] = []
    interval = 1.0 / max(fps, 1)

    ki = 0
    t = 0.0
    end_t = keyframes[-1].t

    while t <= end_t + 1e-6:
        while ki < len(keyframes) - 2 and keyframes[ki + 1].t < t:
            ki += 1

        a = keyframes[ki]
        b = keyframes[min(ki + 1, len(keyframes) - 1)]
        span = max(b.t - a.t, 1e-8)
        p = max(0.0, min(1.0, (t - a.t) / span))

        points.append({
            "lat": _lerp(a.lat, b.lat, p),
            "lng": _lerp(a.lng, b.lng, p),
            "alt": _lerp(a.alt_m, b.alt_m, p),
            "heading": _lerp_angle(a.heading_deg, b.heading_deg, p),
            "tilt": _lerp(a.tilt_deg, b.tilt_deg, p),
            "duration": interval,
        })
        t += interval

    if points:
        points[0]["duration"] = 0.0

    return points


def export_kml(
    shot: ShotPlan,
    output_path: Path,
    *,
    fps: int = 2,
) -> None:
    """ShotPlan을 KML Tour 파일로 내보내기.

    fps가 낮을수록 파일이 작고 부드러운 투어가 됩니다 (기본 2fps).
    Earth Studio / Google Earth에서 불러올 수 있습니다.
    """
    GX = "http://www.google.com/kml/ext/2.2"
    KML_NS = "http://www.opengis.net/kml/2.2"

    ET.register_namespace("", KML_NS)
    ET.register_namespace("gx", GX)

    kml = ET.Element(f"{{{KML_NS}}}kml")

    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = shot.title
    ET.SubElement(doc, "description").text = shot.notes

    # Target placemark
    pm = ET.SubElement(doc, "Placemark")
    ET.SubElement(pm, "name").text = "Target"
    point = ET.SubElement(pm, "Point")
    ET.SubElement(point, "coordinates").text = f"{shot.target_lng},{shot.target_lat},0"

    # Camera tour
    tour = ET.SubElement(doc, f"{{{GX}}}Tour")
    ET.SubElement(tour, "name").text = shot.title
    playlist = ET.SubElement(tour, f"{{{GX}}}Playlist")

    points = _interpolate_for_tour(shot.keyframes, fps)

    for pt in points:
        fly_to = ET.SubElement(playlist, f"{{{GX}}}FlyTo")
        ET.SubElement(fly_to, f"{{{GX}}}duration").text = f"{pt['duration']:.4f}"
        ET.SubElement(fly_to, f"{{{GX}}}flyToMode").text = "smooth"

        camera = ET.SubElement(fly_to, "Camera")
        ET.SubElement(camera, "longitude").text = f"{pt['lng']:.10f}"
        ET.SubElement(camera, "latitude").text = f"{pt['lat']:.10f}"
        ET.SubElement(camera, "altitude").text = f"{pt['alt']:.2f}"
        ET.SubElement(camera, "heading").text = f"{pt['heading']:.4f}"
        ET.SubElement(camera, "tilt").text = f"{pt['tilt']:.4f}"
        ET.SubElement(camera, "roll").text = "0"
        ET.SubElement(camera, "altitudeMode").text = "absolute"

    raw_xml = ET.tostring(kml, encoding="unicode", xml_declaration=False)
    pretty = minidom.parseString(raw_xml).toprettyxml(indent="  ", encoding="utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pretty)
