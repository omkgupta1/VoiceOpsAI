-- 0006: real airport and airline reference data.
--
-- Loaded from OpenFlights (openflights.org), an open dataset of real airports,
-- airlines and routes. Replaces the invented three-letter codes the Phase 1 seed
-- used with genuine ones, so DEL is Indira Gandhi International in Delhi rather
-- than a string that merely looks like an airport.
--
-- Two things this buys beyond realism:
--   * The agent can say "Delhi" instead of reading out "D, E, L", because the
--     city name is now a fact in the database rather than something the model
--     would have to know.
--   * `airlines.icao` maps an IATA flight number (AI303) to the ICAO callsign
--     live aircraft actually broadcast (AIC303), which is what OpenSky indexes.
--
-- Populated by `make reference`, which caches the downloads under data/reference/.

CREATE TABLE airports (
    iata      char(3)      PRIMARY KEY,
    icao      char(4),
    name      text         NOT NULL,
    city      text,
    country   text,
    latitude  numeric(9, 6),
    longitude numeric(9, 6),
    timezone  text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX airports_city_idx    ON airports (city);
CREATE INDEX airports_country_idx ON airports (country);

CREATE TABLE airlines (
    iata     char(2) PRIMARY KEY,
    -- The code a transponder broadcasts: AI -> AIC, 6E -> IGO, SG -> SEJ.
    -- Without this an IATA flight number cannot be matched to a live aircraft.
    icao     char(3),
    name     text    NOT NULL,
    callsign text,
    country  text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX airlines_icao_idx ON airlines (icao);

-- Real route pairs, so a seeded flight flies somewhere the carrier actually goes.
CREATE TABLE routes (
    id           bigserial PRIMARY KEY,
    airline_iata char(2)   NOT NULL,
    origin       char(3)   NOT NULL,
    destination  char(3)   NOT NULL,

    CONSTRAINT routes_unique UNIQUE (airline_iata, origin, destination),
    CONSTRAINT routes_not_circular CHECK (origin <> destination)
);

CREATE INDEX routes_airline_idx ON routes (airline_iata);
CREATE INDEX routes_pair_idx    ON routes (origin, destination);
