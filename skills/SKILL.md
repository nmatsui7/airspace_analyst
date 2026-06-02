---
name: opensky-aviation
description: >
  Use this skill whenever fetching, parsing, or storing live ADS-B flight
  state data from the OpenSky Network REST API. Covers bounding box queries,
  state vector field mapping, rate limit handling, anomaly detection thresholds,
  and SQLite schema conventions for flight snapshots.
license: MIT
---

# OpenSky Network — Aviation Data Skill

## Overview

The OpenSky Network provides free, unauthenticated access to real-time
ADS-B state vectors for all tracked aircraft worldwide.

- **Base URL:** `https://opensky-network.org/api`
- **Auth:** None required for anonymous use
- **Rate limit:** 1 request per 10 seconds (anonymous); 400 credits/day
- **Format:** JSON

---

## Key Endpoint

### GET /states/all

Retrieve current state vectors for all aircraft, optionally filtered by
bounding box.

```
GET https://opensky-network.org/api/states/all
    ?lamin=<lat_min>&lomin=<lon_min>&lamax=<lat_max>&lomax=<lon_max>
```

**US West Coast bounding box** (LAX / SFO / SEA corridor):
```python
BBOX = {
    "lamin": 32.5,   # South of San Diego
    "lomin": -125.0, # Pacific coast
    "lamax": 49.0,   # Canadian border
    "lomax": -114.0  # East of Sierra Nevada
}
```

**Response structure:**
```json
{
  "time": 1234567890,
  "states": [
    [icao24, callsign, origin_country, time_position, last_contact,
     longitude, latitude, baro_altitude, on_ground, velocity,
     true_track, vertical_rate, sensors, geo_altitude, squawk,
     spi, position_source]
  ]
}
```

---

## State Vector Field Index

| Index | Field           | Type    | Units / Notes                          |
|-------|-----------------|---------|----------------------------------------|
| 0     | icao24          | str     | ICAO 24-bit hex transponder address    |
| 1     | callsign        | str     | Flight callsign (may be null/spaces)   |
| 2     | origin_country  | str     | Country of origin                      |
| 3     | time_position   | int     | Unix timestamp of last position update |
| 4     | last_contact    | int     | Unix timestamp of last contact         |
| 5     | longitude       | float   | Decimal degrees WGS-84                 |
| 6     | latitude        | float   | Decimal degrees WGS-84                 |
| 7     | baro_altitude   | float   | Barometric altitude in metres          |
| 8     | on_ground       | bool    | True if surface position               |
| 9     | velocity        | float   | Ground speed in m/s                    |
| 10    | true_track      | float   | Heading in degrees (0=N, clockwise)    |
| 11    | vertical_rate   | float   | Climb/descent rate in m/s              |
| 12    | sensors         | list    | Receiver IDs (may be null)             |
| 13    | geo_altitude    | float   | GPS altitude in metres                 |
| 14    | squawk          | str     | Mode C squawk code                     |
| 15    | spi             | bool    | Special purpose indicator              |
| 16    | position_source | int     | 0=ADS-B, 1=ASTERIX, 2=MLAT, 3=FLARM  |

---

## Unit Conversions

```python
# Altitude: metres → feet
alt_ft = baro_altitude * 3.28084

# Velocity: m/s → knots
speed_kts = velocity * 1.94384

# Vertical rate: m/s → ft/min
vrate_fpm = vertical_rate * 196.85
```

---

## Anomaly Detection Thresholds

Use these as the baseline for the Analyst agent's pattern detection.

| Metric              | Normal Range         | Anomaly Threshold              |
|---------------------|----------------------|-------------------------------|
| Cruise altitude     | 25,000–42,000 ft     | < 10,000 ft (airborne, fast)  |
| Ground speed        | 400–560 kts (cruise) | > 600 kts or < 50 kts airborne|
| Vertical rate       | ±500 fpm (cruise)    | > 2,000 fpm or < -3,000 fpm  |
| Squawk emergency    | N/A                  | 7500, 7600, 7700               |

**Emergency squawk codes:**
- `7500` — Hijacking
- `7600` — Radio failure
- `7700` — General emergency

---

## SQLite Schema

```sql
CREATE TABLE snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at   TEXT NOT NULL,       -- ISO 8601 UTC
    region       TEXT NOT NULL,       -- e.g. 'US_WEST_COAST'
    aircraft_count INTEGER NOT NULL,
    raw_json     TEXT NOT NULL        -- full API response JSON
);

CREATE TABLE state_vectors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     INTEGER REFERENCES snapshots(id),
    icao24          TEXT,
    callsign        TEXT,
    origin_country  TEXT,
    longitude       REAL,
    latitude        REAL,
    baro_altitude_m REAL,
    baro_altitude_ft REAL,
    on_ground       INTEGER,          -- 0/1
    velocity_ms     REAL,
    velocity_kts    REAL,
    true_track      REAL,
    vertical_rate_ms REAL,
    vertical_rate_fpm REAL,
    geo_altitude_m  REAL,
    squawk          TEXT,
    position_source INTEGER,
    is_emergency    INTEGER           -- 0/1: squawk in 7500/7600/7700
);
```

---

## Fetching Pattern (Python)

```python
import httpx, time

OPENSKY_URL = "https://opensky-network.org/api/states/all"
BBOX = {"lamin": 32.5, "lomin": -125.0, "lamax": 49.0, "lomax": -114.0}
RETRY_WAIT = 12   # seconds — stay well clear of 10s rate limit

def fetch_states(bbox: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = httpx.get(OPENSKY_URL, params=bbox, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(RETRY_WAIT)
        except httpx.RequestError as e:
            if attempt < retries - 1:
                time.sleep(RETRY_WAIT)
    return None
```

---

## Null Handling

Many fields can be `null` — always guard before arithmetic:

```python
alt_ft  = (sv[7] * 3.28084)  if sv[7]  is not None else None
spd_kts = (sv[9] * 1.94384)  if sv[9]  is not None else None
vrate   = (sv[11] * 196.85)  if sv[11] is not None else None
```

---

## Known Limitations

- Anonymous rate limit: 1 call per 10 seconds; respect this strictly
- Position data may lag 5–15 seconds behind reality
- Callsign field often contains trailing spaces — always `.strip()`
- `on_ground=True` aircraft are less useful for airspace analysis; filter them out for anomaly detection
- Coverage depends on volunteer ADS-B receivers; remote oceanic areas have gaps
