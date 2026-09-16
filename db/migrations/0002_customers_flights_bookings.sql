-- 0002: the flight domain the voice agent actually services.
-- These tables back the mock flight service built in Phase 2.

CREATE TABLE customers (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name   text        NOT NULL,
    -- Phone is the natural key for a voice channel: it is what identifies
    -- a caller before they have said anything.
    phone       text        NOT NULL UNIQUE,
    email       text        UNIQUE,
    tier        text        NOT NULL DEFAULT 'STANDARD',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE flights (
    id                  uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    flight_number       text          NOT NULL,
    airline             text          NOT NULL,
    origin              char(3)       NOT NULL,
    destination         char(3)       NOT NULL,
    scheduled_departure timestamptz   NOT NULL,
    scheduled_arrival   timestamptz   NOT NULL,
    actual_departure    timestamptz,
    actual_arrival      timestamptz,
    status              flight_status NOT NULL DEFAULT 'SCHEDULED',
    delay_minutes       integer       NOT NULL DEFAULT 0,
    gate                text,
    terminal            text,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now(),

    -- A flight number repeats daily, so it is only unique per departure.
    CONSTRAINT flights_number_departure_key UNIQUE (flight_number, scheduled_departure),
    CONSTRAINT flights_delay_non_negative   CHECK (delay_minutes >= 0),
    CONSTRAINT flights_arrival_after_departure CHECK (scheduled_arrival > scheduled_departure)
);

CREATE INDEX flights_flight_number_idx ON flights (flight_number);
CREATE INDEX flights_route_idx         ON flights (origin, destination);
CREATE INDEX flights_departure_idx     ON flights (scheduled_departure);

CREATE TABLE bookings (
    id            uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The six-character record locator a caller reads out loud.
    pnr           char(6)        NOT NULL UNIQUE,
    customer_id   uuid           NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
    flight_id     uuid           NOT NULL REFERENCES flights (id)   ON DELETE RESTRICT,
    status        booking_status NOT NULL DEFAULT 'CONFIRMED',
    seat          text,
    cabin         text           NOT NULL DEFAULT 'ECONOMY',
    fare_amount   numeric(10, 2) NOT NULL,
    currency      char(3)        NOT NULL DEFAULT 'INR',
    -- Drives cancellation eligibility, which the agent must check before acting.
    is_refundable boolean        NOT NULL DEFAULT false,
    booked_at     timestamptz    NOT NULL DEFAULT now(),
    cancelled_at  timestamptz,
    created_at    timestamptz    NOT NULL DEFAULT now(),
    updated_at    timestamptz    NOT NULL DEFAULT now(),

    CONSTRAINT bookings_fare_non_negative CHECK (fare_amount >= 0),
    -- A cancelled booking must carry a cancellation time, and vice versa.
    CONSTRAINT bookings_cancelled_consistency CHECK (
        (status = 'CANCELLED' AND cancelled_at IS NOT NULL)
        OR (status <> 'CANCELLED' AND cancelled_at IS NULL)
    )
);

CREATE INDEX bookings_customer_idx ON bookings (customer_id);
CREATE INDEX bookings_flight_idx   ON bookings (flight_id);
CREATE INDEX bookings_status_idx   ON bookings (status);

CREATE TABLE refunds (
    id           uuid           PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id   uuid           NOT NULL REFERENCES bookings (id) ON DELETE CASCADE,
    reference    text           NOT NULL UNIQUE,
    amount       numeric(10, 2) NOT NULL,
    currency     char(3)        NOT NULL DEFAULT 'INR',
    status       refund_status  NOT NULL DEFAULT 'PENDING',
    requested_at timestamptz    NOT NULL DEFAULT now(),
    processed_at timestamptz,
    created_at   timestamptz    NOT NULL DEFAULT now(),
    updated_at   timestamptz    NOT NULL DEFAULT now(),

    CONSTRAINT refunds_amount_non_negative CHECK (amount >= 0)
);

CREATE INDEX refunds_booking_idx ON refunds (booking_id);
CREATE INDEX refunds_status_idx  ON refunds (status);

CREATE TRIGGER customers_set_updated_at BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER flights_set_updated_at BEFORE UPDATE ON flights
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER bookings_set_updated_at BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER refunds_set_updated_at BEFORE UPDATE ON refunds
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
