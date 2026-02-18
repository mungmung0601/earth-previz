from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
USER_AGENT = "aerial-cinematic-bot/1.0"


def _nominatim(query: str) -> tuple[float, float, str] | None:
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "limit": "1",
        "addressdetails": "1",
    })
    url = f"{NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not data:
        return None

    r = data[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", query)


def _google_geocode(query: str, api_key: str) -> tuple[float, float, str] | None:
    params = urllib.parse.urlencode({
        "address": query,
        "key": api_key,
    })
    url = f"{GOOGLE_GEOCODE_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    results = data.get("results", [])
    if not results:
        return None

    loc = results[0]["geometry"]["location"]
    name = results[0].get("formatted_address", query)
    return float(loc["lat"]), float(loc["lng"]), name


def geocode(query: str, google_api_key: str | None = None) -> tuple[float, float, str]:
    """장소 이름/주소를 위도, 경도로 변환. (lat, lng, display_name) 반환.

    Nominatim을 먼저 시도하고, 실패 시 Google Geocoding API를 사용.
    """
    result = _nominatim(query)
    if result:
        return result

    key = google_api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if key:
        result = _google_geocode(query, key)
        if result:
            return result

    raise ValueError(
        f"장소를 찾을 수 없습니다: '{query}'\n"
        "팁: 더 일반적인 이름이나 주소로 시도해보세요. "
        "(예: 'Times Square New York', '서울시청')"
    )
