"""
Booking lookup, cancellation and rescheduling — overview §4.

The eligibility rules here matter more than they look. They are what makes the
voice agent's confirmation gate meaningful: an agent that cannot be told "no"
by the backend will happily cancel something it should not have.
"""

from __future__ import annotations

import random
import string
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app import errors
from app.config import settings
from app.db import execute, fetch_all, fetch_one

router = APIRouter(prefix="/v1/bookings", tags=["bookings"])

BOOKING_SELECT = """
    SELECT b.id, b.pnr, b.status, b.seat, b.cabin, b.fare_amount, b.currency,
           b.is_refundable, b.booked_at, b.cancelled_at,
           c.full_name, c.phone, c.email,
           f.id AS flight_id, f.flight_number, f.airline, f.origin, f.destination,
           f.scheduled_departure, f.scheduled_arrival, f.status AS flight_status,
           f.delay_minutes, f.gate, f.terminal
    FROM bookings b
    JOIN customers c ON c.id = b.customer_id
    JOIN flights   f ON f.id = b.flight_id
    WHERE upper(b.pnr) = upper(%s)
"""


class RescheduleRequest(BaseModel):
    flight_id: str


def _shape(row: dict) -> dict:
    return {
        "pnr": row["pnr"],
        "status": row["status"],
        "seat": row["seat"],
        "cabin": row["cabin"],
        "fare_amount": float(row["fare_amount"]),
        "currency": row["currency"],
        "is_refundable": row["is_refundable"],
        "booked_at": row["booked_at"],
        "cancelled_at": row["cancelled_at"],
        "passenger": {
            "name": row["full_name"],
            "phone": row["phone"],
            "email": row["email"],
        },
        "flight": {
            "id": str(row["flight_id"]),
            "flight_number": row["flight_number"],
            "airline": row["airline"],
            "origin": row["origin"],
            "destination": row["destination"],
            "scheduled_departure": row["scheduled_departure"],
            "scheduled_arrival": row["scheduled_arrival"],
            "status": row["flight_status"],
            "delay_minutes": row["delay_minutes"],
            "gate": row["gate"],
            "terminal": row["terminal"],
        },
    }


async def _load(pnr: str) -> dict:
    row = await fetch_one(BOOKING_SELECT, (pnr,))
    if row is None:
        raise errors.booking_not_found(pnr)
    return row


@router.get("/{pnr}")
async def get_booking(pnr: str) -> dict:
    return _shape(await _load(pnr))


@router.post("/{pnr}/cancel")
async def cancel_booking(pnr: str) -> dict:
    row = await _load(pnr)

    if row["status"] == "CANCELLED":
        raise errors.already_cancelled(pnr)
    if row["status"] == "COMPLETED":
        raise errors.not_cancellable("This booking has already been flown and cannot be cancelled")

    # Cancelling minutes before departure is not something a voice agent should
    # be able to do unsupervised, so the backend refuses it outright.
    cutoff = datetime.now(timezone.utc) + timedelta(hours=settings.cancellation_cutoff_hours)
    if row["scheduled_departure"] < cutoff:
        raise errors.not_cancellable(
            f"Cancellation closes {settings.cancellation_cutoff_hours} hours before departure"
        )

    now = datetime.now(timezone.utc)
    await execute(
        "UPDATE bookings SET status='CANCELLED', cancelled_at=%s WHERE id=%s",
        (now, row["id"]),
    )

    refund = None
    if row["is_refundable"]:
        reference = "RF" + "".join(random.choices(string.digits, k=6))
        created = await execute(
            """
            INSERT INTO refunds (booking_id, reference, amount, currency, status)
            VALUES (%s, %s, %s, %s, 'PENDING')
            RETURNING reference, amount, currency, status, requested_at
            """,
            (row["id"], reference, row["fare_amount"], row["currency"]),
        )
        refund = {
            "reference": created["reference"],
            "amount": float(created["amount"]),
            "currency": created["currency"],
            "status": created["status"],
            "requested_at": created["requested_at"],
        }

    return {
        "pnr": row["pnr"],
        "status": "CANCELLED",
        "cancelled_at": now,
        "refund": refund,
        "message": (
            "Booking cancelled. A refund has been initiated."
            if refund
            else "Booking cancelled. This fare is non-refundable, so no refund applies."
        ),
    }


@router.get("/{pnr}/reschedule-options")
async def reschedule_options(pnr: str) -> dict:
    row = await _load(pnr)

    if row["status"] != "CONFIRMED":
        raise errors.not_reschedulable(f"A booking in state {row['status']} cannot be rescheduled")

    options = await fetch_all(
        """
        SELECT id, flight_number, airline, origin, destination,
               scheduled_departure, scheduled_arrival, status
        FROM flights
        WHERE origin = %s AND destination = %s
          AND id <> %s
          AND scheduled_departure > now() + interval '3 hours'
          AND status NOT IN ('CANCELLED', 'DIVERTED')
        ORDER BY scheduled_departure
        LIMIT 5
        """,
        (row["origin"], row["destination"], row["flight_id"]),
    )
    if not options:
        raise errors.no_alternatives(pnr)

    return {
        "pnr": row["pnr"],
        "current_flight": {
            "flight_number": row["flight_number"],
            "scheduled_departure": row["scheduled_departure"],
        },
        "options": [
            {
                "flight_id": str(o["id"]),
                "flight_number": o["flight_number"],
                "airline": o["airline"],
                "origin": o["origin"],
                "destination": o["destination"],
                "scheduled_departure": o["scheduled_departure"],
                "scheduled_arrival": o["scheduled_arrival"],
                "status": o["status"],
            }
            for o in options
        ],
    }


@router.post("/{pnr}/reschedule")
async def reschedule_booking(pnr: str, body: RescheduleRequest) -> dict:
    row = await _load(pnr)

    if row["status"] != "CONFIRMED":
        raise errors.not_reschedulable(f"A booking in state {row['status']} cannot be rescheduled")

    # Validate before the query. Passing a flight number here raises deep inside
    # psycopg and surfaces as a 500 — which a retry engine reads as "transient"
    # and retries forever, when in fact the request can never succeed.
    try:
        uuid.UUID(body.flight_id)
    except ValueError:
        raise errors.invalid_identifier("flight_id", body.flight_id) from None

    target = await fetch_one(
        """
        SELECT id, flight_number, origin, destination,
               scheduled_departure, scheduled_arrival, status
        FROM flights WHERE id = %s
        """,
        (body.flight_id,),
    )
    if target is None:
        raise errors.flight_not_found(body.flight_id)

    # Rescheduling changes when you fly, never where. Letting a voice agent
    # silently move someone to a different city would be a real incident.
    if (target["origin"], target["destination"]) != (row["origin"], row["destination"]):
        raise errors.not_reschedulable("The replacement flight must serve the same route")
    if target["status"] in ("CANCELLED", "DIVERTED"):
        raise errors.not_reschedulable("The replacement flight is not operating")
    if target["scheduled_departure"] <= datetime.now(timezone.utc):
        raise errors.not_reschedulable("The replacement flight has already departed")

    await execute(
        "UPDATE bookings SET flight_id=%s, status='RESCHEDULED' WHERE id=%s",
        (target["id"], row["id"]),
    )

    return {
        "pnr": row["pnr"],
        "status": "RESCHEDULED",
        "previous_flight": {
            "flight_number": row["flight_number"],
            "scheduled_departure": row["scheduled_departure"],
        },
        "new_flight": {
            "flight_id": str(target["id"]),
            "flight_number": target["flight_number"],
            "scheduled_departure": target["scheduled_departure"],
            "scheduled_arrival": target["scheduled_arrival"],
        },
        "message": f"Booking moved to {target['flight_number']}.",
    }
