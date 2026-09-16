-- 0004: asynchronous work and everything that goes wrong with it.
-- This is the durable mirror of the Redis queue built in Phase 6. Redis holds
-- the *live* queue; these tables hold the history that Redis deliberately forgets.

CREATE TABLE jobs (
    id              uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id         uuid         REFERENCES calls (id)     ON DELETE SET NULL,
    customer_id     uuid         REFERENCES customers (id) ON DELETE SET NULL,
    job_type        text         NOT NULL,
    priority        job_priority NOT NULL DEFAULT 'MEDIUM',
    status          job_status   NOT NULL DEFAULT 'SCHEDULED',
    payload         jsonb        NOT NULL DEFAULT '{}'::jsonb,

    -- Guards against duplicate execution (overview.md §1). A caller that
    -- retries an enqueue with the same key gets the original job back rather
    -- than a second cancellation of the same booking.
    idempotency_key text         UNIQUE,

    scheduled_at    timestamptz  NOT NULL DEFAULT now(),
    queued_at       timestamptz,
    started_at      timestamptz,
    completed_at    timestamptz,

    attempt_count   integer      NOT NULL DEFAULT 0,
    max_attempts    integer      NOT NULL DEFAULT 5,
    next_retry_at   timestamptz,
    last_error      text,
    result          jsonb,

    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT jobs_attempts_non_negative CHECK (attempt_count >= 0),
    CONSTRAINT jobs_max_attempts_positive CHECK (max_attempts > 0)
);

CREATE INDEX jobs_status_idx     ON jobs (status);
CREATE INDEX jobs_priority_idx   ON jobs (priority, scheduled_at);
CREATE INDEX jobs_call_idx       ON jobs (call_id);
CREATE INDEX jobs_created_at_idx ON jobs (created_at DESC);
-- Partial indexes: the scheduler and retry sweeper poll these constantly and
-- only ever care about a small slice of the table.
CREATE INDEX jobs_due_idx   ON jobs (scheduled_at)  WHERE status = 'SCHEDULED';
CREATE INDEX jobs_retry_idx ON jobs (next_retry_at) WHERE status = 'RETRY_WAIT';

-- One row per attempt. `jobs` tells you the current state; this tells you the
-- story — which the dashboard's retry-history view and any "why did this take
-- four minutes" question both need.
CREATE TABLE job_attempts (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id         uuid        NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    attempt_number integer     NOT NULL,
    status         job_status  NOT NULL,
    -- Which worker process ran it, for correlating against container logs.
    worker_id      text,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    duration_ms    integer,
    error_class    error_class,
    error_type     text,
    error_message  text,
    -- How long the backoff before this attempt was, so the curve is auditable.
    backoff_ms     integer,

    CONSTRAINT job_attempts_unique   UNIQUE (job_id, attempt_number),
    CONSTRAINT job_attempts_positive CHECK (attempt_number > 0)
);

CREATE INDEX job_attempts_job_idx ON job_attempts (job_id, attempt_number);

-- Failures worth operator attention, from any source — not only jobs.
-- A voice call can fail without any job existing (STT timed out mid-turn).
CREATE TABLE failures (
    id                uuid              PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id            uuid              REFERENCES jobs (id)  ON DELETE CASCADE,
    call_id           uuid              REFERENCES calls (id) ON DELETE CASCADE,
    -- Which dependency broke: flight-api, stt, llm, tts, postgres, redis.
    service           text              NOT NULL,
    error_class       error_class       NOT NULL DEFAULT 'UNKNOWN',
    error_type        text              NOT NULL,
    error_message     text,
    retry_count       integer           NOT NULL DEFAULT 0,
    resolution_status resolution_status NOT NULL DEFAULT 'OPEN',
    context           jsonb             NOT NULL DEFAULT '{}'::jsonb,
    occurred_at       timestamptz       NOT NULL DEFAULT now(),
    resolved_at       timestamptz,

    CONSTRAINT failures_retry_non_negative CHECK (retry_count >= 0)
);

CREATE INDEX failures_service_idx     ON failures (service, occurred_at DESC);
CREATE INDEX failures_job_idx         ON failures (job_id);
CREATE INDEX failures_call_idx        ON failures (call_id);
CREATE INDEX failures_occurred_at_idx ON failures (occurred_at DESC);
CREATE INDEX failures_open_idx        ON failures (occurred_at DESC)
    WHERE resolution_status IN ('OPEN', 'RETRYING');

CREATE TRIGGER jobs_set_updated_at BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
