-- 0005: dashboard operators and the tickets they work.
-- `users` are staff logging into the servicing dashboard (Phase 7/8) — distinct
-- from `customers`, who are the people calling in and never log in anywhere.

CREATE TABLE users (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text        NOT NULL UNIQUE,
    password_hash text        NOT NULL,
    full_name     text        NOT NULL,
    role          user_role   NOT NULL DEFAULT 'USER',
    is_active     boolean     NOT NULL DEFAULT true,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX users_role_idx ON users (role);

CREATE TABLE support_tickets (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Human-readable reference the agent can read back over the phone.
    reference   text        NOT NULL UNIQUE,
    call_id     uuid        REFERENCES calls (id)     ON DELETE SET NULL,
    customer_id uuid        REFERENCES customers (id) ON DELETE SET NULL,
    subject     text        NOT NULL,
    description text,
    status      text        NOT NULL DEFAULT 'OPEN',
    priority    text        NOT NULL DEFAULT 'MEDIUM',
    assigned_to uuid        REFERENCES users (id) ON DELETE SET NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,

    CONSTRAINT support_tickets_status_valid
        CHECK (status IN ('OPEN', 'IN_PROGRESS', 'WAITING', 'RESOLVED', 'CLOSED')),
    CONSTRAINT support_tickets_priority_valid
        CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT'))
);

CREATE INDEX support_tickets_status_idx   ON support_tickets (status, created_at DESC);
CREATE INDEX support_tickets_customer_idx ON support_tickets (customer_id);
CREATE INDEX support_tickets_assignee_idx ON support_tickets (assigned_to);

CREATE TRIGGER users_set_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER support_tickets_set_updated_at BEFORE UPDATE ON support_tickets
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
