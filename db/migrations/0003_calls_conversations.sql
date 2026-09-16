-- 0003: the voice interaction record — what was said, what was decided, how long it took.
-- This is the table the servicing dashboard (Phase 8) is built on.

CREATE TABLE calls (
    id                uuid              PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable: a caller is not always identified, and never is on the first turn.
    customer_id       uuid              REFERENCES customers (id) ON DELETE SET NULL,
    status            call_status       NOT NULL DEFAULT 'INITIATED',
    channel           text              NOT NULL DEFAULT 'browser',
    -- The intent the call ultimately resolved to, e.g. CANCEL_BOOKING.
    primary_intent    text,
    -- Which configurable flow (Phase 5) handled this call.
    flow_id           text,
    escalation_status escalation_status NOT NULL DEFAULT 'NONE',
    escalation_reason text,
    started_at        timestamptz       NOT NULL DEFAULT now(),
    ended_at          timestamptz,
    -- Maintained by Postgres rather than by the application, so it can never
    -- drift from the timestamps it is derived from.
    duration_ms       integer GENERATED ALWAYS AS (
        CASE WHEN ended_at IS NULL THEN NULL
             ELSE (EXTRACT(EPOCH FROM (ended_at - started_at)) * 1000)::integer
        END
    ) STORED,
    summary           text,
    metadata          jsonb             NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz       NOT NULL DEFAULT now(),
    updated_at        timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT calls_ended_after_started CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX calls_customer_idx   ON calls (customer_id);
CREATE INDEX calls_status_idx     ON calls (status);
CREATE INDEX calls_intent_idx     ON calls (primary_intent);
CREATE INDEX calls_started_at_idx ON calls (started_at DESC);
-- Partial index: the dashboard's "needs attention" view only ever asks for these.
CREATE INDEX calls_needs_attention_idx ON calls (started_at DESC)
    WHERE status IN ('FAILED', 'ESCALATED');

CREATE TABLE conversations (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id     uuid        NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    turn_index  integer     NOT NULL,
    speaker     speaker     NOT NULL,
    message     text        NOT NULL,
    -- Intent detected on this specific turn (the call-level one may differ).
    intent      text,
    confidence  numeric(4, 3),
    -- Tools the LLM chose on this turn, with arguments and results.
    tool_calls  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- Per-stage latency. Recorded from the first turn so the Phase 9 metrics
    -- and the "where is the time going" question have real data behind them.
    stt_ms      integer,
    llm_ms      integer,
    tts_ms      integer,
    -- Which provider actually served this turn (local vs groq vs gemini),
    -- so a latency regression can be attributed rather than guessed at.
    providers   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT conversations_turn_unique   UNIQUE (call_id, turn_index),
    CONSTRAINT conversations_turn_positive CHECK (turn_index >= 0),
    CONSTRAINT conversations_confidence_range CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    )
);

CREATE INDEX conversations_call_idx ON conversations (call_id, turn_index);

CREATE TRIGGER calls_set_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
