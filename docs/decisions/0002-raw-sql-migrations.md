# 0002 — Raw SQL migrations as the single schema source

**Status:** Accepted (applies from Phase 1)

## Context

Two services in two languages share one PostgreSQL database: Node/TypeScript for the
platform API, Python for the AI service and workers. Each has a natural ORM (Drizzle,
SQLAlchemy), and each ORM wants to own the schema. Letting both own it guarantees drift.

## Decision

The schema lives in `db/migrations/*.sql` as hand-written SQL, applied by a small runner.
Neither ORM generates migrations. Drizzle and SQLAlchemy each define **read/write models
that map onto** that schema.

## Consequences

- One source of truth; no possibility of two migration histories disagreeing.
- Language-neutral — a third service in any language could join without renegotiation.
- You learn actual DDL, indexes and constraints rather than an ORM's abstraction of them.
- Cost: no auto-generated migrations. Schema changes are written by hand, and the ORM
  models must be updated to match. Accepted deliberately — this is a learning project.
