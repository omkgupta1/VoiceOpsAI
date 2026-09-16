"""
Live external data: real weather and real aircraft in the air right now.

Two public sources, neither needing a key:

  * **NOAA Aviation Weather** — METAR observations, updated roughly hourly.
  * **OpenSky Network** — ADS-B positions broadcast by aircraft themselves.

Both are proxied through this service rather than called from the AI service,
for three reasons: the caching belongs in one place, the chaos engine can then
break them like anything else, and the AI service keeps a single downstream
instead of learning about the internet.

**Everything is cached.** OpenSky rate-limits anonymous callers to a few hundred
requests a day, and a voice agent asking once per turn would exhaust that in an
afternoon. METAR only changes hourly, so caching it is free accuracy.

Both sources are outside our control, so failures here are normal rather than
exceptional and are reported as retryable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app import errors

METAR_URL = "https://aviationweather.gov/api/data/metar"
OPENSKY_URL = "https://opensky-network.org/api/states/all"

# METAR is issued about hourly; OpenSky positions go stale in seconds, but a
# caller asking "where is my flight" twice in a minute does not need two
# round trips against a shared free quota.
WEATHER_TTL = 600.0
AIRCRAFT_TTL = 30.0

# Roughly the Indian subcontinent. A bounding box keeps one OpenSky response
# small enough to cache whole and serve many flight lookups from.
INDIA_BBOX = {"lamin": 6.0, "lomin": 67.0, "lamax": 36.0, "lomax": 98.0}


@dataclass(slots=True)
class _Cached:
    value: Any
    expires_at: float


_cache: dict[str, _Cached] = {}


def _get(key: str) -> Any | None:
    entry = _cache.get(key)
    if entry is None or entry.expires_at < time.time():
        return None
    return entry.value


def _put(key: str, value: Any, ttl: float) -> None:
    _cache[key] = _Cached(value=value, expires_at=time.time() + ttl)


def cache_state() -> dict[str, Any]:
    now = time.time()
    return {
        "entries": len(_cache),
        "keys": {k: round(max(0.0, v.expires_at - now), 1) for k, v in _cache.items()},
    }


async def fetch_metar(icao: str) -> dict[str, Any] | None:
    """Latest observation for an airport, by ICAO code (DEL is VIDP)."""
    key = f"metar:{icao}"
    if (cached := _get(key)) is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                METAR_URL, params={"ids": icao, "format": "json"}
            )
    except httpx.HTTPError as exc:
        raise errors.ServiceError(
            503, "WEATHER_UNAVAILABLE", f"Could not reach the weather service: {exc}", True
        ) from exc

    if response.status_code >= 500:
        raise errors.ServiceError(
            503, "WEATHER_UNAVAILABLE",
            f"Weather service returned {response.status_code}", True,
        )
    if response.status_code >= 400:
        raise errors.ServiceError(
            502, "WEATHER_UNAVAILABLE", "Weather service rejected the request", True
        )

    try:
        reports = response.json()
    except ValueError:
        raise errors.ServiceError(
            502, "WEATHER_UNAVAILABLE", "Weather service returned a body that is not JSON", True
        ) from None

    report = reports[0] if isinstance(reports, list) and reports else None
    # Cached even when empty: an airport with no station does not acquire one in
    # ten minutes, and re-asking spends quota to learn the same thing.
    _put(key, report, WEATHER_TTL)
    return report


async def fetch_aircraft_states() -> list[list[Any]]:
    """Every aircraft currently transmitting over the region, cached whole."""
    key = "opensky:states"
    if (cached := _get(key)) is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(OPENSKY_URL, params=INDIA_BBOX)
    except httpx.HTTPError as exc:
        raise errors.ServiceError(
            503, "AIRCRAFT_DATA_UNAVAILABLE",
            f"Could not reach the aircraft tracking network: {exc}", True,
        ) from exc

    # 429 is the expected failure on a free anonymous quota, not an unusual one.
    if response.status_code == 429:
        raise errors.ServiceError(
            429, "AIRCRAFT_DATA_RATE_LIMITED",
            "Aircraft tracking network is rate limiting us", True,
        )
    if response.status_code >= 400:
        raise errors.ServiceError(
            503, "AIRCRAFT_DATA_UNAVAILABLE",
            f"Aircraft tracking network returned {response.status_code}", True,
        )

    try:
        states = response.json().get("states") or []
    except ValueError:
        raise errors.ServiceError(
            502, "AIRCRAFT_DATA_UNAVAILABLE",
            "Aircraft tracking network returned a body that is not JSON", True,
        ) from None

    _put(key, states, AIRCRAFT_TTL)
    return states


def find_callsign(states: list[list[Any]], callsign: str) -> dict[str, Any] | None:
    """
    Locate one aircraft among the live states.

    OpenSky pads callsigns to eight characters, so a naive equality check never
    matches. The index positions are fixed by its state-vector format.
    """
    wanted = callsign.strip().upper()
    for state in states:
        if not state or len(state) < 12:
            continue
        broadcast = (state[1] or "").strip().upper()
        if broadcast != wanted:
            continue
        return {
            "callsign": broadcast,
            "icao24": state[0],
            "origin_country": state[2],
            "longitude": state[5],
            "latitude": state[6],
            "barometric_altitude_m": state[7],
            "on_ground": state[8],
            "velocity_ms": state[9],
            "true_track_deg": state[10],
            "vertical_rate_ms": state[11],
            "last_contact": state[4],
        }
    return None
