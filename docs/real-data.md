# Real data

Three public sources, none needing a key or a signup.

| Source | What it gives | Freshness |
|---|---|---|
| [OpenFlights](https://openflights.org) | 6,072 airports, 833 airlines, 63,873 routes | Static; **routes are a 2014 snapshot** |
| [NOAA Aviation Weather](https://aviationweather.gov) | METAR observations for any airport | ~hourly |
| [OpenSky Network](https://opensky-network.org) | Live ADS-B positions broadcast by aircraft | Seconds |

```bash
make reference          # load OpenFlights into Postgres (cached in data/reference/)
make reference REFRESH=1
make seed               # rebuild flights on real routes
```

## What is real and what is not

**Bookings can never be real.** No public API exposes passenger records — that is
private airline data. So `get_booking`, `cancel_booking`, `reschedule_booking` and
`check_refund_status` remain simulated, which is also the only safe option: you do
not want a learning project cancelling someone's flight.

| | Real | Simulated |
|---|---|---|
| Airports, airlines, routes | ● | |
| Weather | ● | |
| Aircraft positions | ● | |
| Flight schedules and delays | | ● |
| Bookings, cancellations, refunds | | ● |

All 40 seeded flights fly a route their carrier actually operated — verified by
joining `flights` against the real `routes` table.

## Two tools reach outside

```
check_airport_weather("DEL")    → NOAA      → 27C, 2.17km visibility, thunderstorms, IFR
check_aircraft_position("AI302") → OpenSky  → airborne, 37,000ft, 918 km/h, heading 134°
```

Both are **proxied through the flight service**, not called from the AI service.
Three reasons: caching lives in one place, the AI service keeps a single
downstream instead of learning about the internet, and the chaos engine can break
them like anything else (verified: `make chaos S=hard_down` returns 503 from the
weather endpoint).

### The IATA/ICAO bridge

A ticket says `AI302`. The aircraft broadcasts `AIC302`. Those are different code
systems — IATA for commerce, ICAO for operations — and without `airlines.icao`
from OpenFlights there is no way to get from one to the other. The same applies
to airports: passengers say `DEL`, NOAA wants `VIDP`.

### Caching is not optional

OpenSky rate-limits anonymous callers to a few hundred requests a day. A voice
agent asking once per turn would exhaust that in an afternoon. So one bounding-box
query covering India is cached for 30 seconds and serves every flight lookup in
that window; METAR is cached for 10 minutes, which costs nothing because it only
changes hourly.

`GET /v1/live/cache` shows what is cached and for how long.

## What this taught us about tool selection

Adding these two tools moved the eval from **79% → 92%**, but not evenly, and the
uneven part is the interesting part:

| State | Before | After adding both | After removing from `servicing` |
|---|---|---|---|
| `identify` | 3 tools | **5 tools — all 15 cases pass** | 5 tools, unchanged |
| `servicing` | 6 tools | 8 tools — *"yes, cancel it"* → `get_booking` ✗ | 6 tools, fixed |

The same two tools **improved** one state and **broke** another. So the cost is
not tool count — it is how *distinguishable* the options are. Weather, position,
schedule and booking lookup are four obviously different questions. `servicing`
was already crowded with overlapping booking operations, and two more blurred it.

They stay in `identify`, where "is my flight delayed by weather" and "has my plane
taken off" are opening questions anyway. A customer who asks mid-servicing is
answered on the next turn.

## Known rough edge

The agent reads coordinates aloud as *"16.6947 latitude and 91.3038 longitude"*,
which is not how a person would say it. A voice-appropriate answer would be
"about 200 kilometres south-east of Kolkata, cruising at 37,000 feet". That needs
reverse geocoding against the airports table — worth doing, not yet done.
