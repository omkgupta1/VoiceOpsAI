-- 0001_init: shared types and helpers used by every later migration.
--
-- Native enums are used rather than text + CHECK. They are self-documenting in
-- pgweb, reject bad values at the database boundary, and cost one byte less per
-- row. The tradeoff: adding a value later needs ALTER TYPE ... ADD VALUE, which
-- cannot run inside a transaction block on older Postgres. Accepted — these
-- state machines are the stable part of the design.

-- ---------- Flight domain ----------

CREATE TYPE flight_status AS ENUM (
    'SCHEDULED', 'ON_TIME', 'DELAYED', 'BOARDING',
    'DEPARTED', 'ARRIVED', 'CANCELLED', 'DIVERTED'
);

CREATE TYPE booking_status AS ENUM (
    'CONFIRMED', 'CANCELLED', 'RESCHEDULED', 'COMPLETED', 'NO_SHOW'
);

CREATE TYPE refund_status AS ENUM (
    'NOT_REQUESTED', 'PENDING', 'PROCESSING', 'COMPLETED', 'REJECTED'
);

-- ---------- Call domain ----------

CREATE TYPE call_status AS ENUM (
    'INITIATED', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'ESCALATED', 'ABANDONED'
);

CREATE TYPE escalation_status AS ENUM (
    'NONE', 'REQUESTED', 'ESCALATED', 'RESOLVED'
);

CREATE TYPE speaker AS ENUM ('CUSTOMER', 'AGENT', 'SYSTEM');

-- ---------- Job / queue domain ----------
--
-- Mirrors the lifecycle in overview.md §8:
--   SCHEDULED -> QUEUED -> PROCESSING -> SUCCESS
--   PROCESSING -> FAILED -> RETRY_WAIT -> QUEUED -> ...
--   retries exhausted -> DEAD_LETTER

CREATE TYPE job_status AS ENUM (
    'SCHEDULED', 'QUEUED', 'PROCESSING', 'SUCCESS',
    'FAILED', 'RETRY_WAIT', 'DEAD_LETTER', 'CANCELLED'
);

CREATE TYPE job_priority AS ENUM ('HIGH', 'MEDIUM', 'LOW');

-- The single most important distinction in this project: retry, or do not.
CREATE TYPE error_class AS ENUM ('RETRYABLE', 'PERMANENT', 'UNKNOWN');

CREATE TYPE resolution_status AS ENUM (
    'OPEN', 'RETRYING', 'RESOLVED', 'ESCALATED', 'DEAD_LETTER'
);

-- ---------- Access control ----------

CREATE TYPE user_role AS ENUM ('USER', 'CX_AGENT', 'SUPERVISOR', 'ADMIN');

-- ---------- Helpers ----------

-- Attached as a BEFORE UPDATE trigger on every table carrying updated_at,
-- so the column is maintained by the database rather than by each service.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
