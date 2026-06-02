# ADS-B State Vector Metadata

Source: OpenSky Network REST API (`/api/states/all`)
Format: Each aircraft is returned as a list of 17 fields (index 0–16).

---

## Core State Vector Fields (from ADS-B transponder)

| Index | Field | Type | Unit | Null? | Description |
|-------|-------|------|------|-------|-------------|
| 0 | `icao24` | string | — | No | ICAO 24-bit transponder address in hexadecimal. Unique per aircraft registration. E.g. `"aa7427"` |
| 1 | `callsign` | string | — | Yes | Flight callsign as set by the crew. Often matches the airline flight number (e.g. `"UAL123"`) but not always. May contain trailing spaces — always `.strip()`. |
| 2 | `origin_country` | string | — | No | Country of aircraft registration, inferred from the ICAO24 address prefix. |
| 3 | `time_position` | integer | Unix timestamp (s) | Yes | UTC timestamp of the last known position update. Null if no position has been received. Used to compute `position_age_s`. |
| 4 | `last_contact` | integer | Unix timestamp (s) | No | UTC timestamp of the last any RF contact from the transponder. May be more recent than `time_position` if only non-positional messages were received. |
| 5 | `longitude` | float | Decimal degrees (WGS-84) | Yes | Aircraft longitude. Negative = West. |
| 6 | `latitude` | float | Decimal degrees (WGS-84) | Yes | Aircraft latitude. Positive = North. |
| 7 | `baro_altitude` | float | Metres | Yes | Barometric altitude. Derived from the aircraft's altimeter. Convert to feet: `× 3.28084`. Stored as both `baro_altitude_m` and `baro_altitude_ft`. |
| 8 | `on_ground` | boolean | — | No | `True` if the aircraft is transmitting a surface position. Airborne anomaly detection should exclude `on_ground = True` records. |
| 9 | `velocity` | float | m/s | Yes | Ground speed. Convert to knots: `× 1.94384`. Stored as both `velocity_ms` and `velocity_kts`. |
| 10 | `true_track` | float | Degrees (0–360) | Yes | Heading over ground. 0° = North, clockwise. Not the same as magnetic heading. |
| 11 | `vertical_rate` | float | m/s | Yes | Climb (+) or descent (−) rate. Convert to ft/min: `× 196.85`. Stored as both `vertical_rate_ms` and `vertical_rate_fpm`. |
| 12 | `sensors` | list | — | Yes | IDs of OpenSky receiver stations that picked up this transmission. Usually null in the anonymous API response. **Not stored.** |
| 13 | `geo_altitude` | float | Metres | Yes | GPS/geometric altitude from the aircraft's GNSS receiver. More accurate than barometric altitude but less commonly available. |
| 14 | `squawk` | string | 4-digit octal code | Yes | Mode C transponder squawk code set by the crew or ATC. Special codes: `7500` = hijacking, `7600` = radio failure, `7700` = general emergency. `1200` = VFR flight (US). |
| 15 | `spi` | boolean | — | No | Special Purpose Indicator. Set when ATC requests the crew to "ident". Rarely used in analysis. **Not stored.** |
| 16 | `position_source` | integer | Enum | No | How the position was determined: `0` = ADS-B, `1` = ASTERIX, `2` = MLAT (multilateration), `3` = FLARM. ADS-B is the most reliable. |

---

## Derived Fields (computed on ingest, stored in SQLite)

| Field | Derived From | Formula | Description |
|-------|-------------|---------|-------------|
| `baro_altitude_ft` | index 7 | `baro_altitude_m × 3.28084` | Barometric altitude in feet — more familiar to aviation practitioners. |
| `velocity_kts` | index 9 | `velocity_ms × 1.94384` | Ground speed in knots. |
| `vertical_rate_fpm` | index 11 | `vertical_rate_ms × 196.85` | Climb/descent rate in feet per minute. |
| `position_age_s` | index 3 + API `time` | `snapshot_time − time_position` | Seconds since the last position fix at the time of the snapshot. Values > 60s flagged as stale. |
| `is_emergency` | index 14 | `squawk IN (7500, 7600, 7700)` | Boolean flag: 1 if an emergency squawk code is active. |

---

## Aircraft Registry Enrichment Fields (from `aircraftDatabase.csv`)

Joined at query time via `icao24`. Populated by running `download_aircraft_db.py`.
Source: [OpenSky Aircraft Database](https://opensky-network.org/datasets/metadata/aircraftDatabase.csv)

| Field | CSV Column | Description |
|-------|-----------|-------------|
| `registration` | `registration` | Aircraft tail number / civil registration. E.g. `"N77296"`. |
| `model` | `model` | Full model name from the manufacturer. E.g. `"737-824"`. |
| `typecode` | `typecode` | ICAO aircraft type designator. E.g. `"B738"` (Boeing 737-800), `"A320"`. Used for type-mix analysis. |
| `operator` | `operator` | Full operator/airline name. E.g. `"United Airlines Inc"`. |
| `operator_icao` | `operatoricao` | ICAO 3-letter airline code. E.g. `"UAL"`. |
| `operator_iata` | `operatoriata` | IATA 2-letter airline code. E.g. `"UA"`. |
| `manufacturer` | `manufacturername` | Aircraft manufacturer name. E.g. `"Boeing"`, `"Airbus"`. |
| `engines` | `engines` | Engine type/series string. E.g. `"CFM INTL. 56-7B27"`. |
| `category` | `categoryDescription` | FAA/ICAO wake turbulence category or aircraft class description. |

---

## Anomaly Detection Thresholds

| Metric | Normal Range | Anomaly Threshold | Stored Flag |
|--------|-------------|-------------------|-------------|
| Barometric altitude (airborne) | 25,000–42,000 ft | < 10,000 ft | `alt_stats.low_count` |
| Ground speed (cruise) | 400–560 kts | > 600 kts | `speed_stats.fast_count` |
| Ground speed (airborne min) | > 80 kts | < 50 kts | `speed_stats.slow_count` |
| Vertical rate (cruise) | ±500 fpm | > 2,000 fpm (climb) | `vr_stats.steep_climb_count` |
| Vertical rate (cruise) | ±500 fpm | < −3,000 fpm (descent) | `vr_stats.steep_descent_count` |
| Position age | < 15 s | > 60 s | `stale_position_count` |
| Squawk code | 1200 (VFR) or ATC-assigned | 7500 / 7600 / 7700 | `is_emergency` |

---

## Emergency Squawk Codes

| Code | Meaning | Action |
|------|---------|--------|
| `7500` | Hijacking | Highest priority alert |
| `7600` | Radio communication failure | Pilot cannot communicate with ATC |
| `7700` | General emergency (distress) | May include fuel, medical, mechanical |
| `1200` | VFR flight (US) | Normal — not an emergency |
| `2000` | Entering radar coverage unannounced | Normal transitional code |

---

## Position Source Enum

| Value | Source | Reliability |
|-------|--------|------------|
| `0` | ADS-B | Highest — GPS-derived, broadcast by aircraft |
| `1` | ASTERIX | Secondary surveillance radar data |
| `2` | MLAT | Multilateration — triangulated from multiple receivers, less accurate |
| `3` | FLARM | Collision avoidance system, primarily gliders/GA |

---

## Notes

- All timestamps are UTC Unix epoch seconds.
- `icao24` is stored lowercase in SQLite for consistent joining with `aircraftDatabase.csv`.
- Fields at indices 12 (`sensors`) and 15 (`spi`) are received but not stored — they are not useful for airspace analysis.
- The aircraft registry enrichment is optional. If `aircraftDatabase.csv` has not been downloaded, the system degrades gracefully: `enriched = False` and registry fields are `null`.
- OpenSky anonymous rate limit: 1 request per 10 seconds, 400 credits/day.
