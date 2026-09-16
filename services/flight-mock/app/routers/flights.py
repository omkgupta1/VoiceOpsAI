"""Flight status lookup — the CHECK_FLIGHT_STATUS workflow (overview §4)."""

from __future__ import annotations

from fastapi import APIRouter

from app import errors
from app.db import fetch_one

router = APIRouter(prefix="/v1/flights", tags=["flights"])


@router.get("/{flight_number}/status")
async def flight_status(flight_number: str) -> dict:
    # A flight number repeats daily, so "the" flight is ambiguous. Callers mean
    # the one nearest to now — today's departure, not last Tuesday's.
    row = await fetch_one(
        """
        SELECT flight_number, airline, origin, destination,
               scheduled_departure, scheduled_arrival,
               actual_departure, actual_arrival,
               status, delay_minutes, gate, terminal
        FROM flights
        WHERE upper(flight_number) = upper(%s)
        ORDER BY abs(extract(epoch FROM (scheduled_departure - now())))
        LIMIT 1
        """,
        (flight_number,),
    )
    if row is None:
        raise errors.flight_not_found(flight_number)

    estimated = row["scheduled_departure"]
    if row["delay_minutes"]:
        from datetime import timedelta

        estimated = estimated + timedelta(minutes=row["delay_minutes"])

    return {
        "flight_number": row["flight_number"],
        "airline": row["airline"],
        "origin": row["origin"],
        "destination": row["destination"],
        "status": row["status"],
        "scheduled_departure": row["scheduled_departure"],
        "estimated_departure": estimated,
        "scheduled_arrival": row["scheduled_arrival"],
        "delay_minutes": row["delay_minutes"],
        "gate": row["gate"],
        "terminal": row["terminal"],
    }
