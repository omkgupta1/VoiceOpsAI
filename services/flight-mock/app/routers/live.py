"""
Live data endpoints: real weather, real aircraft positions.

These surface genuinely external sources, so unlike the rest of this service they
can fail for reasons nobody here controls. Every failure is reported as retryable,
which is honest: the flight is still up there, we just could not see it this second.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter

from app import errors, live
from app.db import fetch_one

router = APIRouter(prefix="/v1/live", tags=["live"])

_NOISE = re.compile(r"[\s\-.,_]+")

# Enough of a METAR to answer "will weather delay my flight" in plain words.
_FLIGHT_RULES = {
    "VFR": "clear",
    "MVFR": "some cloud or reduced visibility",
    "IFR": "poor visibility — delays are likely",
    "LIFR": "very poor visibility — delays are very likely",
}


@router.get("/weather/{airport}")
async def airport_weather(airport: str) -> dict[str, Any]:
    """Current observed weather at an airport, by IATA code."""
    code = _NOISE.sub("", airport).upper()

    # NOAA indexes by ICAO; passengers and flight numbers use IATA. The airports
    # table is what bridges the two.
    row = await fetch_one(
        "SELECT iata, icao, name, city, country FROM airports WHERE iata = %s", (code,)
    )
    if row is None:
        raise errors.ServiceError(
            404, "AIRPORT_NOT_FOUND", f"No airport matches {airport}", False
        )
    if not row["icao"]:
        raise errors.ServiceError(
            422, "NO_WEATHER_STATION",
            f"{row['name']} has no weather station we can query", False,
        )

    report = await live.fetch_metar(row["icao"])
    if report is None:
        raise errors.ServiceError(
            404, "NO_OBSERVATION",
            f"No recent weather observation for {row['city'] or code}", False,
        )

    rules = report.get("fltCat")
    return {
        "airport": {"iata": row["iata"], "icao": row["icao"], "name": row["name"], "city": row["city"]},
        "observed_at": report.get("reportTime"),
        "temperature_c": report.get("temp"),
        "dewpoint_c": report.get("dewp"),
        "wind_speed_kt": report.get("wspd"),
        "wind_direction_deg": report.get("wdir"),
        "visibility": report.get("visib"),
        "flight_rules": rules,
        "conditions": _FLIGHT_RULES.get(rules or "", "unknown"),
        "raw": report.get("rawOb"),
        "source": "NOAA Aviation Weather",
    }


@router.get("/aircraft/{flight_number}")
async def aircraft_position(flight_number: str) -> dict[str, Any]:
    """Where an aircraft is right now, if it is airborne and transmitting."""
    number = _NOISE.sub("", flight_number).upper()
    match = re.match(r"^([A-Z0-9]{2})(\d{1,4})$", number)
    if not match:
        raise errors.ServiceError(
            422, "INVALID_IDENTIFIER",
            f"{flight_number} is not a flight number like AI302 or 6E455", False,
        )

    carrier, digits = match.groups()
    airline = await fetch_one(
        "SELECT iata, icao, name FROM airlines WHERE iata = %s", (carrier,)
    )
    if airline is None or not airline["icao"]:
        raise errors.ServiceError(
            404, "AIRLINE_NOT_FOUND",
            f"No airline matches the code {carrier}", False,
        )

    # Aircraft broadcast the ICAO code, not the IATA one printed on a ticket:
    # AI303 is in the air as AIC303.
    callsign = f"{airline['icao']}{digits}"
    found = live.find_callsign(await live.fetch_aircraft_states(), callsign)

    if found is None:
        # Not an error. Most flights are on the ground most of the time, and
        # "not airborne" is a true and useful answer.
        return {
            "flight_number": number,
            "airline": airline["name"],
            "callsign": callsign,
            "airborne": False,
            "message": (
                f"{number} is not currently transmitting a position. It is most likely "
                "on the ground, or outside the region we track."
            ),
            "source": "OpenSky Network",
        }

    altitude = found["barometric_altitude_m"]
    velocity = found["velocity_ms"]
    return {
        "flight_number": number,
        "airline": airline["name"],
        "callsign": callsign,
        "airborne": not found["on_ground"],
        "latitude": found["latitude"],
        "longitude": found["longitude"],
        "altitude_m": altitude,
        "altitude_ft": round(altitude * 3.28084) if altitude else None,
        # Metres per second means nothing said aloud; km/h does.
        "ground_speed_kmh": round(velocity * 3.6) if velocity else None,
        "heading_deg": found["true_track_deg"],
        "climbing": (found["vertical_rate_ms"] or 0) > 1,
        "descending": (found["vertical_rate_ms"] or 0) < -1,
        "source": "OpenSky Network",
    }


@router.get("/cache")
async def cache() -> dict[str, Any]:
    """What is cached and for how long — both sources are rate limited."""
    return live.cache_state()
