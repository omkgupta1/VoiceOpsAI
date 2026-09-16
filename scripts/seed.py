#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "bcrypt>=4.2"]
# ///
"""
Load realistic sample data.

Deliberately covers every table, including calls, jobs, attempts and failures,
so the servicing dashboard has something to render long before the voice
pipeline is producing real traffic — and so queue and analytics queries can be
developed against data shaped like production.

Destructive: truncates the domain tables first. Deterministic: same data every
run, so a bug reproduces.

Usage:  uv run scripts/seed.py
"""

from __future__ import annotations

import os
import random
import string
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import psycopg

ROOT = Path(__file__).resolve().parent.parent
GREEN, DIM, RESET = "\033[32m", "\033[2m", "\033[0m"

random.seed(42)
NOW = datetime.now(timezone.utc)
SEED_PASSWORD = "voiceops123"

# Reference tables (airports, airlines, routes) are deliberately absent: they
# hold real data loaded by `make reference` and must survive a reseed.
TABLES = [
    "job_attempts", "failures", "jobs", "conversations", "calls",
    "support_tickets", "refunds", "bookings", "flights", "customers", "users",
]

# The network the seeded schedule flies. Real airports; the routes between them
# are whatever the carriers actually operated.
HUBS = ("DEL", "BOM", "BLR", "MAA", "CCU", "HYD", "GOI", "PNQ", "AMD", "COK")

CUSTOMERS = [
    ("Ananya Sharma", "+919812345001", "ananya.sharma@example.com", "GOLD"),
    ("Rahul Verma", "+919812345002", "rahul.verma@example.com", "STANDARD"),
    ("Priya Nair", "+919812345003", "priya.nair@example.com", "PLATINUM"),
    ("Arjun Mehta", "+919812345004", "arjun.mehta@example.com", "STANDARD"),
    ("Sneha Iyer", "+919812345005", "sneha.iyer@example.com", "GOLD"),
    ("Vikram Singh", "+919812345006", "vikram.singh@example.com", "STANDARD"),
    ("Kavya Reddy", "+919812345007", "kavya.reddy@example.com", "STANDARD"),
    ("Imran Khan", "+919812345008", "imran.khan@example.com", "GOLD"),
    ("Meera Joshi", "+919812345009", "meera.joshi@example.com", "STANDARD"),
    ("Aditya Bose", "+919812345010", "aditya.bose@example.com", "PLATINUM"),
    ("Nisha Gupta", "+919812345011", "nisha.gupta@example.com", "STANDARD"),
    ("Karthik Menon", "+919812345012", "karthik.menon@example.com", "STANDARD"),
]

STAFF = [
    ("admin@voiceops.ai", "Om Gupta", "ADMIN"),
    ("supervisor@voiceops.ai", "Divya Rao", "SUPERVISOR"),
    ("agent1@voiceops.ai", "Rohan Das", "CX_AGENT"),
    ("agent2@voiceops.ai", "Fatima Sheikh", "CX_AGENT"),
]

INTENTS = [
    "CHECK_FLIGHT_STATUS", "CANCEL_BOOKING", "RESCHEDULE_FLIGHT",
    "CHECK_REFUND_STATUS", "GET_BOOKING_DETAILS", "CREATE_TICKET", "SPEAK_TO_HUMAN",
]

JOB_TYPES = [
    "scheduled_callback", "retry_cancellation", "refund_status_poll",
    "followup_survey", "escalation_notify",
]

# Failures we can actually produce in Phase 2 by turning on the chaos engine.
# Split by class because the two are never interchangeable: a RETRYABLE error
# earns a backoff and another attempt, a PERMANENT one must fail immediately.
# Seeding a permanent error with a retry history would contradict the very rule
# this system exists to enforce.
RETRYABLE_MODES = [
    ("flight-api", "UpstreamTimeout", "Flight service did not respond within 5000ms"),
    ("flight-api", "ServiceUnavailable", "Flight service returned 503"),
    ("flight-api", "RateLimited", "Flight service returned 429"),
    ("llm", "ModelTimeout", "LLM did not return within 30000ms"),
    ("stt", "TranscriptionFailed", "Audio chunk could not be decoded"),
    ("tts", "SynthesisFailed", "Voice model returned an empty buffer"),
    ("postgres", "ConnectionReset", "Connection reset by peer"),
]

PERMANENT_MODES = [
    ("flight-api", "BookingNotFound", "No booking matches the supplied PNR"),
    ("flight-api", "NotCancellable", "Fare rules forbid cancellation within 2h of departure"),
    ("flight-api", "AlreadyCancelled", "Booking was already cancelled"),
    ("flight-api", "Unauthorized", "Caller is not the passenger on this booking"),
]


def load_database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("DATABASE_URL not set and not found in .env — run `make setup`")


def pnr() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def load_routes(conn: psycopg.Connection) -> list[tuple[str, str, str, str]]:
    """
    Real (carrier, name, origin, destination) tuples between the hub airports.

    Flights are built from these rather than by pairing random airport codes, so
    a seeded AI303 flies a route Air India genuinely operated. Requires
    `make reference` to have run.
    """
    rows = conn.execute(
        """
        SELECT r.airline_iata, a.name, r.origin, r.destination
          FROM routes r
          JOIN airlines a ON a.iata = r.airline_iata
         WHERE r.origin = ANY(%s) AND r.destination = ANY(%s)
           AND a.country = 'India'
         ORDER BY r.airline_iata, r.origin, r.destination
        """,
        (list(HUBS), list(HUBS)),
    ).fetchall()

    if not rows:
        sys.exit(
            "No routes found — run `make reference` first to load real airport "
            "and airline data from OpenFlights."
        )
    # This connection has no dict row factory, so rows arrive as plain tuples
    # in the order selected: (airline_iata, name, origin, destination).
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def seed(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}

    conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")

    # ---------- staff ----------
    pw_hash = bcrypt.hashpw(SEED_PASSWORD.encode(), bcrypt.gensalt(rounds=10)).decode()
    user_ids = [
        conn.execute(
            "INSERT INTO users (email, password_hash, full_name, role) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (email, pw_hash, name, role),
        ).fetchone()[0]
        for email, name, role in STAFF
    ]
    counts["users"] = len(user_ids)

    # ---------- customers ----------
    customer_ids = [
        conn.execute(
            "INSERT INTO customers (full_name, phone, email, tier) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            row,
        ).fetchone()[0]
        for row in CUSTOMERS
    ]
    counts["customers"] = len(customer_ids)

    # ---------- flights ----------
    # Built on real routes, spread across the past two days and the next five,
    # with a realistic mix of on-time, delayed and cancelled so status queries
    # have variety to work with.
    route_pool = load_routes(conn)
    flights: list[tuple] = []
    for i in range(40):
        code, airline, origin, destination = random.choice(route_pool)
        departure = NOW + timedelta(days=random.randint(-2, 5), hours=random.randint(0, 23))
        duration = timedelta(minutes=random.randint(75, 210))

        roll = random.random()
        if roll < 0.55:
            status, delay = ("ON_TIME", 0)
        elif roll < 0.80:
            status, delay = ("DELAYED", random.choice([15, 25, 40, 55, 90, 120]))
        elif roll < 0.88:
            status, delay = ("CANCELLED", 0)
        elif departure < NOW:
            status, delay = ("ARRIVED", 0)
        else:
            status, delay = ("SCHEDULED", 0)

        flight_id = conn.execute(
            """
            INSERT INTO flights (flight_number, airline, origin, destination,
                                 scheduled_departure, scheduled_arrival, status,
                                 delay_minutes, gate, terminal)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                f"{code}{random.randint(100, 999)}", airline, origin, destination,
                departure, departure + duration, status, delay,
                f"{random.choice('ABCD')}{random.randint(1, 24)}",
                str(random.randint(1, 3)),
            ),
        ).fetchone()[0]
        flights.append((flight_id, status, departure))
    counts["flights"] = len(flights)

    # ---------- bookings ----------
    bookings: list[tuple] = []
    used_pnrs: set[str] = set()
    for _ in range(60):
        while (code := pnr()) in used_pnrs:
            pass
        used_pnrs.add(code)

        customer_id = random.choice(customer_ids)
        flight_id, flight_status, departure = random.choice(flights)
        refundable = random.random() < 0.45

        if flight_status == "CANCELLED" or random.random() < 0.18:
            status, cancelled_at = "CANCELLED", NOW - timedelta(hours=random.randint(1, 72))
        elif departure < NOW:
            status, cancelled_at = "COMPLETED", None
        else:
            status, cancelled_at = "CONFIRMED", None

        booking_id = conn.execute(
            """
            INSERT INTO bookings (pnr, customer_id, flight_id, status, seat, cabin,
                                  fare_amount, is_refundable, booked_at, cancelled_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                code, customer_id, flight_id, status,
                f"{random.randint(1, 32)}{random.choice('ABCDEF')}",
                random.choices(["ECONOMY", "PREMIUM", "BUSINESS"], weights=[80, 14, 6])[0],
                round(random.uniform(3200, 28500), 2), refundable,
                NOW - timedelta(days=random.randint(3, 60)), cancelled_at,
            ),
        ).fetchone()[0]
        bookings.append((booking_id, customer_id, status, refundable))
    counts["bookings"] = len(bookings)

    # ---------- refunds (only for cancelled, refundable bookings) ----------
    refunds = 0
    for booking_id, _, status, refundable in bookings:
        if status != "CANCELLED" or not refundable:
            continue
        refund_status = random.choices(
            ["PENDING", "PROCESSING", "COMPLETED", "REJECTED"], weights=[25, 25, 45, 5]
        )[0]
        requested = NOW - timedelta(days=random.randint(1, 20))
        conn.execute(
            """
            INSERT INTO refunds (booking_id, reference, amount, status, requested_at, processed_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                booking_id, f"RF{random.randint(100000, 999999)}",
                round(random.uniform(2500, 24000), 2), refund_status, requested,
                requested + timedelta(days=random.randint(1, 7))
                if refund_status in ("COMPLETED", "REJECTED") else None,
            ),
        )
        refunds += 1
    counts["refunds"] = refunds

    # ---------- calls + conversations ----------
    call_ids: list[str] = []
    turns_total = 0
    for _ in range(80):
        customer_id = random.choice(customer_ids)
        intent = random.choice(INTENTS)
        started = NOW - timedelta(hours=random.randint(0, 168), minutes=random.randint(0, 59))

        status = random.choices(
            ["COMPLETED", "ESCALATED", "FAILED", "ABANDONED", "IN_PROGRESS"],
            weights=[68, 12, 8, 7, 5],
        )[0]
        ended = None if status == "IN_PROGRESS" else started + timedelta(seconds=random.randint(25, 320))

        if status == "ESCALATED":
            esc_status = "ESCALATED"
            esc_reason = random.choice([
                "Customer explicitly requested a human agent",
                "Low-confidence intent detection over three turns",
                "Repeated backend failures on cancellation",
            ])
        else:
            esc_status, esc_reason = "NONE", None

        call_id = conn.execute(
            """
            INSERT INTO calls (customer_id, status, channel, primary_intent, flow_id,
                               escalation_status, escalation_reason, started_at, ended_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                customer_id, status, "browser", intent, intent.lower(),
                esc_status, esc_reason, started, ended,
            ),
        ).fetchone()[0]
        call_ids.append(call_id)

        # Alternating customer/agent turns with per-stage latency recorded.
        for turn in range(random.randint(2, 8)):
            is_customer = turn % 2 == 0
            conn.execute(
                """
                INSERT INTO conversations (call_id, turn_index, speaker, message, intent,
                                           confidence, stt_ms, llm_ms, tts_ms, providers, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    call_id, turn,
                    "CUSTOMER" if is_customer else "AGENT",
                    "Can you check whether my flight to Delhi is delayed?"
                    if is_customer else
                    "Your flight is currently scheduled to depart on time at 14:30.",
                    intent if is_customer else None,
                    round(random.uniform(0.62, 0.99), 3) if is_customer else None,
                    random.randint(180, 900) if is_customer else None,
                    None if is_customer else random.randint(400, 2600),
                    None if is_customer else random.randint(150, 700),
                    '{"stt":"local","llm":"ollama","tts":"piper"}',
                    started + timedelta(seconds=turn * 12),
                ),
            )
            turns_total += 1
    counts["calls"] = len(call_ids)
    counts["conversations"] = turns_total

    # ---------- jobs, attempts, failures ----------
    attempts_total = failures_total = 0
    for _ in range(50):
        status = random.choices(
            ["SUCCESS", "SCHEDULED", "QUEUED", "PROCESSING", "RETRY_WAIT", "DEAD_LETTER", "FAILED"],
            weights=[46, 14, 8, 5, 12, 8, 7],
        )[0]

        # A FAILED job is the one place a permanent error belongs, and when it
        # does, the job stopped after a single attempt — no backoff, no retry.
        is_permanent = status == "FAILED" and random.random() < 0.6
        if is_permanent:
            service, etype, emsg = random.choice(PERMANENT_MODES)
            error_class, attempts = "PERMANENT", 1
        else:
            service, etype, emsg = random.choice(RETRYABLE_MODES)
            error_class = "RETRYABLE"
            attempts = {"SUCCESS": random.randint(1, 3), "DEAD_LETTER": 5,
                        "RETRY_WAIT": random.randint(1, 4), "FAILED": random.randint(2, 4)}.get(status, 0)

        scheduled = NOW + timedelta(hours=random.randint(-72, 24))

        job_id = conn.execute(
            """
            INSERT INTO jobs (call_id, customer_id, job_type, priority, status, payload,
                              idempotency_key, scheduled_at, attempt_count, next_retry_at, last_error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                random.choice(call_ids), random.choice(customer_ids),
                random.choice(JOB_TYPES),
                random.choices(["HIGH", "MEDIUM", "LOW"], weights=[25, 50, 25])[0],
                status, '{"source":"seed"}',
                f"seed-{random.randint(10**9, 10**10)}", scheduled, attempts,
                NOW + timedelta(seconds=random.choice([2, 4, 8, 16, 32]))
                if status == "RETRY_WAIT" else None,
                emsg if status in ("RETRY_WAIT", "DEAD_LETTER", "FAILED") else None,
            ),
        ).fetchone()[0]

        for attempt in range(1, attempts + 1):
            final = attempt == attempts
            succeeded = final and status == "SUCCESS"
            started_at = scheduled + timedelta(seconds=attempt * 20)
            duration = random.randint(120, 5200)

            conn.execute(
                """
                INSERT INTO job_attempts (job_id, attempt_number, status, worker_id, started_at,
                                          finished_at, duration_ms, error_class, error_type,
                                          error_message, backoff_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    job_id, attempt, "SUCCESS" if succeeded else "FAILED",
                    f"worker-{random.randint(1, 4)}", started_at,
                    started_at + timedelta(milliseconds=duration), duration,
                    None if succeeded else error_class,
                    None if succeeded else etype,
                    None if succeeded else emsg,
                    # The exponential curve from overview.md §9: 0, 2s, 4s, 8s, 16s.
                    0 if attempt == 1 else 2 ** (attempt - 1) * 1000,
                ),
            )
            attempts_total += 1

            if not succeeded:
                conn.execute(
                    """
                    INSERT INTO failures (job_id, service, error_class, error_type, error_message,
                                          retry_count, resolution_status, context, occurred_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        job_id, service, error_class, etype, emsg, attempt,
                        # Only the last attempt carries the job's terminal
                        # resolution. Earlier ones were still retrying when they
                        # happened, and stamping the final status onto all of
                        # them invents a history where the system gave up five
                        # times instead of once.
                        (
                            "RESOLVED" if status == "SUCCESS" else
                            "DEAD_LETTER" if status == "DEAD_LETTER" else
                            "RETRYING" if status == "RETRY_WAIT" else "OPEN"
                        ) if final else "RETRYING",
                        '{"source":"seed"}', started_at,
                    ),
                )
                failures_total += 1
    counts["jobs"] = 50
    counts["job_attempts"] = attempts_total
    counts["failures"] = failures_total

    # ---------- support tickets ----------
    for i in range(14):
        conn.execute(
            """
            INSERT INTO support_tickets (reference, call_id, customer_id, subject, description,
                                         status, priority, assigned_to)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                f"TKT-{2000 + i}", random.choice(call_ids), random.choice(customer_ids),
                random.choice([
                    "Refund not received after 14 days",
                    "Unable to reschedule cancelled flight",
                    "Charged twice for one booking",
                    "Voice agent could not verify booking",
                ]),
                "Raised automatically from a voice call that could not be completed.",
                random.choice(["OPEN", "IN_PROGRESS", "WAITING", "RESOLVED"]),
                random.choices(["LOW", "MEDIUM", "HIGH", "URGENT"], weights=[20, 45, 25, 10])[0],
                random.choice(user_ids),
            ),
        )
    counts["support_tickets"] = 14

    return counts


def main() -> None:
    with psycopg.connect(load_database_url()) as conn:
        counts = seed(conn)
        conn.commit()

    print()
    for table, count in counts.items():
        print(f"  {GREEN}seeded{RESET}   {table:<18} {count:>5}")
    print(f"\n  {DIM}Staff logins: {', '.join(e for e, _, _ in STAFF)}{RESET}")
    print(f"  {DIM}Password for all seeded users: {SEED_PASSWORD}{RESET}\n")


if __name__ == "__main__":
    main()
