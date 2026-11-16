from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sgp4.api import Satrec, jday


BASE_DIR = Path(__file__).resolve().parent
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
USER_AGENT = "BRAHMAND-LiveWatch/1.0 (public data dashboard)"
TLE_CACHE_SECONDS = 300
MAX_TLE_AGE_DAYS = 7
_tle_cache: tuple[float, list[tuple[str, str, str]], str] | None = None

app = FastAPI(
    title="BRAHMAND LiveWatch",
    description="Earth intelligence dashboard backed by public data feeds.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


async def fetch_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                url, params=params, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Upstream data source unavailable: {exc}"
        ) from exc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "BRAHMAND LiveWatch"}


@app.get("/api/earthquakes")
async def earthquakes() -> dict[str, Any]:
    data = await fetch_json(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    )
    features = []
    for item in data.get("features", []):
        props = item.get("properties") or {}
        coords = (item.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        magnitude = props.get("mag")
        features.append(
            {
                "id": item.get("id"),
                "title": props.get("title") or "Earthquake",
                "place": props.get("place"),
                "magnitude": magnitude,
                "depth_km": coords[2] if len(coords) > 2 else None,
                "longitude": coords[0],
                "latitude": coords[1],
                "time": props.get("time"),
                "url": props.get("url"),
                "severity_basis": "Heuristic based on USGS magnitude.",
                "severity": (
                    "critical"
                    if magnitude is not None and magnitude >= 6
                    else "high"
                    if magnitude is not None and magnitude >= 4.5
                    else "moderate"
                    if magnitude is not None and magnitude >= 2.5
                    else "low"
                ),
            }
        )
    return {
        "source": "USGS",
        "authenticity": "LIVE",
        "retrieved_at": utc_now().isoformat(),
        "count": len(features),
        "events": features,
    }


def event_coordinates(geometry: dict[str, Any]) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if geometry.get("type") == "Point" and len(coords) >= 2:
        return float(coords[1]), float(coords[0])

    points: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            points.append((float(value[1]), float(value[0])))
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(coords)
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


@app.get("/api/disasters")
async def disasters() -> dict[str, Any]:
    data = await fetch_json(
        "https://eonet.gsfc.nasa.gov/api/v3/events",
        params={"status": "open", "limit": 100},
    )
    events = []
    for item in data.get("events", []):
        geometries = item.get("geometry") or []
        latest = geometries[-1] if geometries else {}
        location = event_coordinates(latest) if latest else None
        events.append(
            {
                "id": item.get("id"),
                "title": item.get("title") or "Natural event",
                "categories": [c.get("title") for c in item.get("categories", [])],
                "time": latest.get("date"),
                "latitude": location[0] if location else None,
                "longitude": location[1] if location else None,
                "url": (item.get("sources") or [{}])[0].get("url"),
                "severity": "unclassified",
                "severity_basis": "NASA EONET does not provide a common severity scale.",
            }
        )
    return {
        "source": "NASA EONET",
        "authenticity": "LIVE",
        "retrieved_at": utc_now().isoformat(),
        "count": len(events),
        "events": events,
    }


@app.get("/api/weather")
async def weather(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    data = await fetch_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "timezone": "UTC",
        },
    )
    return {
        "source": "Open-Meteo",
        "authenticity": "LIVE",
        "retrieved_at": utc_now().isoformat(),
        "location": {
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        },
        "current": data.get("current"),
    }


@app.get("/api/satellites")
async def satellites(limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    records, source = await fetch_tle_records()
    satellites_data = []
    malformed = 0
    stale = 0
    now = utc_now()
    jd, fraction = jday(
        now.year, now.month, now.day, now.hour, now.minute,
        now.second + now.microsecond / 1_000_000,
    )
    julian_date = jd + fraction
    for name, line1, line2 in records:
        if not (line1.startswith("1 ") and line2.startswith("2 ")):
            malformed += 1
            continue
        try:
            satellite = Satrec.twoline2rv(line1, line2)
            tle_age_days = julian_date - satellite.jdsatepoch
            if tle_age_days > MAX_TLE_AGE_DAYS:
                stale += 1
                continue
            error, position, _velocity = satellite.sgp4(jd, fraction)
            if error:
                malformed += 1
                continue
            x, y, z = position
            centuries = (julian_date - 2451545.0) / 36525.0
            sidereal_angle = math.radians(
                (
                    280.46061837
                    + 360.98564736629 * (julian_date - 2451545.0)
                    + 0.000387933 * centuries**2
                    - centuries**3 / 38710000.0
                )
                % 360.0
            )
            earth_fixed_x = math.cos(sidereal_angle) * x + math.sin(sidereal_angle) * y
            earth_fixed_y = -math.sin(sidereal_angle) * x + math.cos(sidereal_angle) * y
            radius = (x * x + y * y + z * z) ** 0.5
            latitude = math.degrees(math.asin(z / radius))
            longitude = math.degrees(math.atan2(earth_fixed_y, earth_fixed_x))
            satellites_data.append(
                {
                    "name": name.removeprefix("0 "),
                    "norad_id": int(line1[2:7]),
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude_km": radius - 6371.0,
                    "tle_age_days": round(max(0.0, tle_age_days), 2),
                }
            )
        except (ValueError, IndexError):
            malformed += 1
            continue
        if len(satellites_data) >= limit:
            break
    if not satellites_data:
        raise HTTPException(
            status_code=502,
            detail="No usable satellite TLEs newer than seven days were returned.",
        )
    return {
        "source": f"{source} / SGP4",
        "authenticity": "DELAYED / DERIVED",
        "retrieved_at": now.isoformat(),
        "position_note": "Estimated from published TLEs; not a direct radar observation. TLEs older than seven days are excluded.",
        "malformed_records": malformed,
        "stale_records": stale,
        "count": len(satellites_data),
        "satellites": satellites_data,
    }


async def fetch_tle_records() -> tuple[list[tuple[str, str, str]], str]:
    global _tle_cache
    cached = _tle_cache
    if cached and time.monotonic() - cached[0] < TLE_CACHE_SECONDS:
        return cached[1], cached[2]

    try:
        text = await fetch_tle_text()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        records = [
            (lines[index], lines[index + 1], lines[index + 2])
            for index in range(0, len(lines) - 2, 3)
        ]
        if not records:
            raise HTTPException(
                status_code=502, detail="CelesTrak returned no usable TLE records."
            )
        source = "CelesTrak"
    except HTTPException as celestrak_error:
        records_by_id: dict[str, tuple[str, str, str]] = {}
        fallback_errors = []
        for search in ("ISS", "CSS"):
            try:
                data = await fetch_json(
                    "https://tle.ivanstanojevic.me/api/tle",
                    params={"search": search},
                )
            except HTTPException as fallback_error:
                fallback_errors.append(str(fallback_error.detail))
                continue

            for item in data.get("member", []) if isinstance(data, dict) else []:
                name = item.get("name")
                line1 = item.get("line1")
                line2 = item.get("line2")
                satellite_id = item.get("satelliteId")
                if all(isinstance(value, str) and value for value in (name, line1, line2)):
                    records_by_id[str(satellite_id or line1[2:7])] = (name, line1, line2)

        if not records_by_id:
            fallback_detail = "; ".join(fallback_errors) or "No TLE records were returned."
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{celestrak_error.detail}; alternate TLE provider unavailable: "
                    f"{fallback_detail}"
                ),
            ) from celestrak_error
        records = list(records_by_id.values())
        source = "TLE API (Ivan Stanojevic)"

    _tle_cache = (time.monotonic(), records, source)
    return records, source


async def fetch_tle_text() -> str:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                "https://celestrak.org/NORAD/elements/gp.php",
                params={"GROUP": "stations", "FORMAT": "tle"},
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response.text
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"CelesTrak data source unavailable: {exc}"
        ) from exc
