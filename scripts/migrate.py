#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2"]
# ///
"""
Minimal SQL migration runner.

Applies `db/migrations/*.sql` in filename order, recording each one in a
`schema_migrations` table. Every migration runs inside a transaction, so a
failing migration leaves the database untouched.

Migrations are checksummed. Editing an already-applied migration is refused —
that is the single most common way to get two databases silently out of sync.

Usage:
    uv run scripts/migrate.py            # apply pending migrations
    uv run scripts/migrate.py status     # show what is applied vs pending
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "db" / "migrations"

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def load_database_url() -> str:
    """Read DATABASE_URL from the environment, falling back to .env."""
    if url := os.environ.get("DATABASE_URL"):
        return url

    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")

    sys.exit(f"{RED}DATABASE_URL not set and not found in .env — run `make setup`{RESET}")


def discover() -> list[Path]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        sys.exit(f"{RED}No .sql files found in {MIGRATIONS_DIR}{RESET}")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ensure_tracking_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     text        PRIMARY KEY,
            checksum    text        NOT NULL,
            applied_at  timestamptz NOT NULL DEFAULT now(),
            duration_ms integer     NOT NULL
        )
        """
    )
    conn.commit()


def applied_map(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {version: digest for version, digest in rows}


def cmd_status(conn: psycopg.Connection) -> None:
    applied = applied_map(conn)
    print()
    for path in discover():
        version = path.stem
        if version not in applied:
            print(f"  {YELLOW}pending{RESET}  {version}")
        elif applied[version] != checksum(path):
            print(f"  {RED}CHANGED{RESET}  {version}  {DIM}(already applied but file was edited){RESET}")
        else:
            print(f"  {GREEN}applied{RESET}  {version}")
    print()


def cmd_up(conn: psycopg.Connection) -> None:
    applied = applied_map(conn)
    pending = []

    for path in discover():
        version = path.stem
        if version in applied:
            if applied[version] != checksum(path):
                sys.exit(
                    f"{RED}Refusing to continue: {version} was already applied but its "
                    f"file has changed.{RESET}\n"
                    f"Write a new migration instead, or `make db-reset` to rebuild from scratch."
                )
            continue
        pending.append(path)

    if not pending:
        print(f"\n  {GREEN}Database is up to date{RESET} — {len(applied)} migration(s) applied\n")
        return

    print()
    for path in pending:
        version = path.stem
        started = time.perf_counter()
        try:
            with conn.transaction():
                conn.execute(path.read_text())
                elapsed = int((time.perf_counter() - started) * 1000)
                conn.execute(
                    "INSERT INTO schema_migrations (version, checksum, duration_ms) VALUES (%s, %s, %s)",
                    (version, checksum(path), elapsed),
                )
        except psycopg.Error as exc:
            print(f"  {RED}failed{RESET}   {version}\n\n{exc}\n")
            sys.exit(1)
        print(f"  {GREEN}applied{RESET}  {version} {DIM}({elapsed}ms){RESET}")

    print(f"\n  {GREEN}{len(pending)} migration(s) applied{RESET}\n")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "up"
    if command not in {"up", "status"}:
        sys.exit(f"Unknown command: {command} (expected 'up' or 'status')")

    with psycopg.connect(load_database_url(), autocommit=True) as conn:
        ensure_tracking_table(conn)
        (cmd_status if command == "status" else cmd_up)(conn)


if __name__ == "__main__":
    main()
