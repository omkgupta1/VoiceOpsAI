"""Refund status lookup — the CHECK_REFUND_STATUS workflow (overview §4)."""

from __future__ import annotations

from fastapi import APIRouter

from app import errors
from app.db import fetch_all, fetch_one

router = APIRouter(prefix="/v1", tags=["refunds"])


def _shape(row: dict) -> dict:
    return {
        "reference": row["reference"],
        "pnr": row["pnr"],
        "amount": float(row["amount"]),
        "currency": row["currency"],
        "status": row["status"],
        "requested_at": row["requested_at"],
        "processed_at": row["processed_at"],
    }


@router.get("/refunds/{reference}")
async def refund_by_reference(reference: str) -> dict:
    row = await fetch_one(
        """
        SELECT r.reference, r.amount, r.currency, r.status,
               r.requested_at, r.processed_at, b.pnr
        FROM refunds r JOIN bookings b ON b.id = r.booking_id
        WHERE upper(r.reference) = upper(%s)
        """,
        (reference,),
    )
    if row is None:
        raise errors.refund_not_found(reference)
    return _shape(row)


@router.get("/bookings/{pnr}/refunds")
async def refunds_for_booking(pnr: str) -> dict:
    booking = await fetch_one("SELECT id FROM bookings WHERE upper(pnr) = upper(%s)", (pnr,))
    if booking is None:
        raise errors.booking_not_found(pnr)

    rows = await fetch_all(
        """
        SELECT r.reference, r.amount, r.currency, r.status,
               r.requested_at, r.processed_at, b.pnr
        FROM refunds r JOIN bookings b ON b.id = r.booking_id
        WHERE r.booking_id = %s ORDER BY r.requested_at DESC
        """,
        (booking["id"],),
    )
    return {"pnr": pnr.upper(), "refunds": [_shape(r) for r in rows]}
