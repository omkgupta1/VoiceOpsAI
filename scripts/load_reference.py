#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "httpx>=0.27"]
# ///
"""
Load real airport, airline and route data from OpenFlights.

OpenFlights (openflights.org) publishes open datasets of the world's airports,
airlines and the routes between them. No key, no rate limit, no signup.

A caveat worth stating rather than discovering later: the **routes** file is a
2014 snapshot, so route pairs are real but historical — Vistara, for instance,
barely existed then and has no domestic routes in it. Airports and airlines are
still accurate; only "who flies where" has aged.

Downloads are cached under data/reference/ so re-running is free.

    uv run scripts/load_reference.py            # load, using the cache
    uv run scripts/load_reference.py --refresh  # re-download first
"""

from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "reference"
BASE = "https://raw.githubusercontent.com/jpatokal/openflights/master/data"

GREEN, DIM, RED, RESET = "\033[32m", "\033[2m", "\033[31m", "\033[0m"

FILES = {"airports": "airports.dat", "airlines": "airlines.dat", "routes": "routes.dat"}

# OpenFlights writes an unquoted \N for null.
NULL = "\\N"


def load_database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("DATABASE_URL not set and not found in .env — run `make setup`")


def fetch(name: str, *, refresh: bool) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / FILES[name]

    if path.exists() and not refresh:
        print(f"  {DIM}cached{RESET}   {FILES[name]:<14} {path.stat().st_size // 1024}KB")
        return path.read_text(encoding="utf-8", errors="replace")

    print(f"  {DIM}fetching{RESET} {FILES[name]}…", end="", flush=True)
    try:
        response = httpx.get(f"{BASE}/{FILES[name]}", timeout=60.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        if path.exists():
            print(f" {RED}failed ({exc}); using the cached copy{RESET}")
            return path.read_text(encoding="utf-8", errors="replace")
        sys.exit(f"\n{RED}Could not download {FILES[name]}: {exc}{RESET}")

    path.write_bytes(response.content)
    print(f" {len(response.content) // 1024}KB")
    return response.text


def clean(value: str) -> str | None:
    value = (value or "").strip()
    return None if value in ("", NULL) else value


def main() -> None:
    refresh = "--refresh" in sys.argv
    print()

    airports_raw = fetch("airports", refresh=refresh)
    airlines_raw = fetch("airlines", refresh=refresh)
    routes_raw = fetch("routes", refresh=refresh)
    print()

    with psycopg.connect(load_database_url()) as conn:
        # ---------- airports ----------
        airports: dict[str, tuple] = {}
        for row in csv.reader(io.StringIO(airports_raw)):
            if len(row) < 12:
                continue
            iata = clean(row[4])
            # Only airports with a IATA code are addressable by a passenger.
            if not iata or len(iata) != 3 or row[12].strip('"') != "airport":
                continue
            try:
                latitude, longitude = float(row[6]), float(row[7])
            except ValueError:
                continue
            airports[iata] = (
                iata, clean(row[5]), row[1], clean(row[2]), clean(row[3]),
                latitude, longitude, clean(row[11]),
            )

        conn.execute("TRUNCATE airports, airlines, routes RESTART IDENTITY CASCADE")
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO airports (iata, icao, name, city, country, latitude, longitude, timezone)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                list(airports.values()),
            )
        print(f"  {GREEN}loaded{RESET}   airports  {len(airports):>6}")

        # ---------- airlines ----------
        airlines: dict[str, tuple] = {}
        for row in csv.reader(io.StringIO(airlines_raw)):
            if len(row) < 8:
                continue
            iata, icao = clean(row[3]), clean(row[4])
            # Active carriers with both codes: the IATA code appears in a flight
            # number, the ICAO code in what the aircraft actually broadcasts.
            if not iata or len(iata) != 2 or not icao or len(icao) != 3:
                continue
            if row[7].strip() != "Y":
                continue
            airlines.setdefault(iata, (iata, icao, row[1], clean(row[5]), clean(row[6])))

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO airlines (iata, icao, name, callsign, country) VALUES (%s,%s,%s,%s,%s)",
                list(airlines.values()),
            )
        print(f"  {GREEN}loaded{RESET}   airlines  {len(airlines):>6}")

        # ---------- routes ----------
        seen: set[tuple[str, str, str]] = set()
        routes: list[tuple[str, str, str]] = []
        for row in csv.reader(io.StringIO(routes_raw)):
            if len(row) < 5:
                continue
            carrier, origin, destination = clean(row[0]), clean(row[2]), clean(row[4])
            if not carrier or not origin or not destination or origin == destination:
                continue
            if len(carrier) != 2 or len(origin) != 3 or len(destination) != 3:
                continue
            # Skip routes we cannot resolve, so the foreign keys stay honest.
            if carrier not in airlines or origin not in airports or destination not in airports:
                continue
            key = (carrier, origin, destination)
            if key in seen:
                continue
            seen.add(key)
            routes.append(key)

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO routes (airline_iata, origin, destination) VALUES (%s,%s,%s)",
                routes,
            )
        print(f"  {GREEN}loaded{RESET}   routes    {len(routes):>6}")
        conn.commit()

    print(f"\n  {DIM}Source: OpenFlights (openflights.org). Routes are a 2014 snapshot.{RESET}\n")


if __name__ == "__main__":
    main()
