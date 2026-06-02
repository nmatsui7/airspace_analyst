"""
tools/db.py — Deterministic ingestor and SQLite helpers.

Ingestion path: OpenSky JSON → SQLite  (zero LLM involvement)
Read path:      SQLite → agents (LLM runs here only)

Enrichment: OpenSky aircraft database CSV (downloaded once via
tools/download_aircraft_db.py) is loaded into SQLite as the
`aircraft` table and joined at query time to add:
  - registration (tail number)
  - model        (e.g. "737-824")
  - typecode     (ICAO type designator, e.g. "B738")
  - operator     (airline name, e.g. "United Airlines Inc")
  - operator_icao (e.g. "UAL")
"""

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH    = Path(__file__).parent.parent / "airspace.db"
AIRCRAFT_DB = Path(__file__).parent.parent / "data" / "aircraftDatabase.csv"

REGIONS = {
    "US_WEST_COAST": {"lamin": 32.5, "lomin": -125.0, "lamax": 49.0, "lomax": -114.0},
}

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at     TEXT NOT NULL,
            region         TEXT NOT NULL,
            aircraft_count INTEGER NOT NULL,
            raw_json       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS state_vectors (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id       INTEGER REFERENCES snapshots(id),
            icao24            TEXT,
            callsign          TEXT,
            origin_country    TEXT,
            time_position     INTEGER,
            last_contact      INTEGER,
            longitude         REAL,
            latitude          REAL,
            baro_altitude_m   REAL,
            baro_altitude_ft  REAL,
            on_ground         INTEGER,
            velocity_ms       REAL,
            velocity_kts      REAL,
            true_track        REAL,
            vertical_rate_ms  REAL,
            vertical_rate_fpm REAL,
            geo_altitude_m    REAL,
            squawk            TEXT,
            position_source   INTEGER,
            is_emergency      INTEGER,
            position_age_s    INTEGER
        );

        -- Aircraft registry enrichment table
        -- Populated once from aircraftDatabase.csv via load_aircraft_db()
        CREATE TABLE IF NOT EXISTS aircraft (
            icao24        TEXT PRIMARY KEY,
            registration  TEXT,
            model         TEXT,
            typecode      TEXT,
            operator      TEXT,
            operator_icao TEXT,
            operator_iata TEXT,
            manufacturer  TEXT,
            engines       TEXT,
            category      TEXT
        );
    """)
    conn.commit()
    conn.close()


# ── Aircraft DB loader ────────────────────────────────────────────────────────

def load_aircraft_db(csv_path: Path | None = None) -> int:
    """
    Load the OpenSky aircraft database CSV into the `aircraft` table.
    Download the CSV from:
      https://opensky-network.org/datasets/metadata/aircraftDatabase.csv

    Returns number of rows loaded.
    Safe to re-run — uses INSERT OR REPLACE.
    """
    path = csv_path or AIRCRAFT_DB
    if not path.exists():
        print(f"[db] Aircraft DB not found at {path}")
        print("[db] Download it from:")
        print("[db]   https://opensky-network.org/datasets/metadata/aircraftDatabase.csv")
        print(f"[db] Save it to: {AIRCRAFT_DB}")
        return 0

    print(f"[db] Loading aircraft database from {path} ...")
    conn = get_conn()
    cur  = conn.cursor()
    count = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            icao24 = (row.get("icao24") or "").strip().lower()
            if not icao24:
                continue
            batch.append((
                icao24,
                (row.get("registration")     or "").strip() or None,
                (row.get("model")            or "").strip() or None,
                (row.get("typecode")         or "").strip() or None,
                (row.get("operator")         or "").strip() or None,
                (row.get("operatoricao")     or "").strip() or None,
                (row.get("operatoriata")     or "").strip() or None,
                (row.get("manufacturername") or "").strip() or None,
                (row.get("engines")          or "").strip() or None,
                (row.get("categoryDescription") or "").strip() or None,
            ))
            # Batch insert every 5000 rows for speed
            if len(batch) >= 5000:
                cur.executemany("""
                    INSERT OR REPLACE INTO aircraft
                    (icao24, registration, model, typecode, operator,
                     operator_icao, operator_iata, manufacturer, engines, category)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, batch)
                count += len(batch)
                batch = []

        if batch:
            cur.executemany("""
                INSERT OR REPLACE INTO aircraft
                (icao24, registration, model, typecode, operator,
                 operator_icao, operator_iata, manufacturer, engines, category)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, batch)
            count += len(batch)

    conn.commit()
    conn.close()
    print(f"[db] ✓ Loaded {count:,} aircraft records.")
    return count


def aircraft_db_loaded() -> bool:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0]
    conn.close()
    return n > 0


# ── State vector parser ───────────────────────────────────────────────────────

def _parse_sv(sv: list, snapshot_time: int | None = None) -> dict:
    """
    Parse a raw OpenSky state vector list into a typed dict.
    Field index mapping from skills/SKILL.md.
    Null-guards all numeric fields before conversion.
    """
    alt_m         = sv[7]
    vel_ms        = sv[9]
    vr_ms         = sv[11]
    squawk        = (sv[14] or "").strip() or None
    time_position = sv[3]
    last_contact  = sv[4]

    if snapshot_time is not None and time_position is not None:
        position_age_s = max(0, snapshot_time - time_position)
    else:
        position_age_s = None

    return {
        "icao24":            (sv[0] or "").strip().lower() or None,
        "callsign":          (sv[1] or "").strip() or None,
        "origin_country":    sv[2],
        "time_position":     time_position,
        "last_contact":      last_contact,
        "longitude":         sv[5],
        "latitude":          sv[6],
        "baro_altitude_m":   alt_m,
        "baro_altitude_ft":  round(alt_m * 3.28084, 0) if alt_m is not None else None,
        "on_ground":         1 if sv[8] else 0,
        "velocity_ms":       vel_ms,
        "velocity_kts":      round(vel_ms * 1.94384, 1) if vel_ms is not None else None,
        "true_track":        sv[10],
        "vertical_rate_ms":  vr_ms,
        "vertical_rate_fpm": round(vr_ms * 196.85, 0) if vr_ms is not None else None,
        "geo_altitude_m":    sv[13],
        "squawk":            squawk,
        "position_source":   sv[16],
        "is_emergency":      1 if squawk in EMERGENCY_SQUAWKS else 0,
        "position_age_s":    position_age_s,
    }


# ── Ingestor ──────────────────────────────────────────────────────────────────

def ingest_snapshot(api_response: dict, region: str = "US_WEST_COAST") -> int:
    """
    Write a raw OpenSky API response to SQLite.
    Returns the snapshot_id.  No LLM — pure deterministic parsing.
    """
    states         = api_response.get("states") or []
    fetched_at     = datetime.now(timezone.utc).isoformat()
    aircraft_count = len(states)
    snapshot_time  = api_response.get("time")

    conn = get_conn()
    cur  = conn.cursor()

    cur.execute(
        "INSERT INTO snapshots (fetched_at, region, aircraft_count, raw_json) VALUES (?,?,?,?)",
        (fetched_at, region, aircraft_count, json.dumps(api_response))
    )
    snapshot_id = cur.lastrowid

    for sv in states:
        if len(sv) < 17:
            continue
        parsed = _parse_sv(sv, snapshot_time=snapshot_time)
        cur.execute("""
            INSERT INTO state_vectors (
                snapshot_id, icao24, callsign, origin_country,
                time_position, last_contact,
                longitude, latitude, baro_altitude_m, baro_altitude_ft,
                on_ground, velocity_ms, velocity_kts, true_track,
                vertical_rate_ms, vertical_rate_fpm, geo_altitude_m,
                squawk, position_source, is_emergency, position_age_s
            ) VALUES (
                :snapshot_id,:icao24,:callsign,:origin_country,
                :time_position,:last_contact,
                :longitude,:latitude,:baro_altitude_m,:baro_altitude_ft,
                :on_ground,:velocity_ms,:velocity_kts,:true_track,
                :vertical_rate_ms,:vertical_rate_fpm,:geo_altitude_m,
                :squawk,:position_source,:is_emergency,:position_age_s
            )
        """, {"snapshot_id": snapshot_id, **parsed})

    conn.commit()
    conn.close()
    return snapshot_id


# ── Snapshot summary (read path) ──────────────────────────────────────────────

def get_snapshot_summary(snapshot_id: int) -> dict:
    """
    Build a rich summary dict for the Analyst agent.
    Joins aircraft enrichment data if the aircraft DB is loaded.
    All arithmetic happens here — the LLM only reasons, never calculates.
    """
    conn = get_conn()

    snap = dict(conn.execute(
        "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone())

    enriched = aircraft_db_loaded()

    # Join aircraft metadata if available
    if enriched:
        airborne_rows = conn.execute("""
            SELECT sv.*,
                   a.registration, a.model, a.typecode,
                   a.operator, a.operator_icao, a.operator_iata,
                   a.manufacturer, a.engines, a.category
            FROM state_vectors sv
            LEFT JOIN aircraft a ON sv.icao24 = a.icao24
            WHERE sv.snapshot_id = ? AND sv.on_ground = 0
        """, (snapshot_id,)).fetchall()
    else:
        airborne_rows = conn.execute("""
            SELECT * FROM state_vectors
            WHERE snapshot_id = ? AND on_ground = 0
        """, (snapshot_id,)).fetchall()

    airborne = [dict(r) for r in airborne_rows]

    # Emergency squawks
    emergencies = [a for a in airborne if a["is_emergency"]]

    # Altitude stats
    alts = [a["baro_altitude_ft"] for a in airborne if a["baro_altitude_ft"] is not None]
    alt_stats = {
        "min_ft":    round(min(alts), 0) if alts else None,
        "max_ft":    round(max(alts), 0) if alts else None,
        "avg_ft":    round(sum(alts) / len(alts), 0) if alts else None,
        "low_count": sum(1 for a in alts if a < 10000),
    }

    # Speed stats
    spds = [a["velocity_kts"] for a in airborne if a["velocity_kts"] is not None]
    spd_stats = {
        "min_kts":    round(min(spds), 1) if spds else None,
        "max_kts":    round(max(spds), 1) if spds else None,
        "avg_kts":    round(sum(spds) / len(spds), 1) if spds else None,
        "fast_count": sum(1 for s in spds if s > 600),
        "slow_count": sum(1 for s in spds if s < 50),
    }

    # Vertical rate anomalies
    vrates = [a["vertical_rate_fpm"] for a in airborne if a["vertical_rate_fpm"] is not None]
    vr_stats = {
        "steep_climb_count":   sum(1 for v in vrates if v > 2000),
        "steep_descent_count": sum(1 for v in vrates if v < -3000),
    }

    # Country breakdown
    countries = {}
    for a in airborne:
        c = a["origin_country"] or "Unknown"
        countries[c] = countries.get(c, 0) + 1
    top_countries = sorted(countries.items(), key=lambda x: -x[1])[:8]

    # Aircraft type breakdown (only if enriched)
    type_counts = {}
    if enriched:
        for a in airborne:
            tc = a.get("typecode") or "Unknown"
            type_counts[tc] = type_counts.get(tc, 0) + 1
    top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:8]

    # Operator breakdown (only if enriched)
    op_counts = {}
    if enriched:
        for a in airborne:
            op = a.get("operator") or a.get("operator_icao") or "Unknown"
            op_counts[op] = op_counts.get(op, 0) + 1
    top_operators = sorted(op_counts.items(), key=lambda x: -x[1])[:8]

    # Low-altitude fast aircraft
    low_fast = [
        a for a in airborne
        if a["baro_altitude_ft"] is not None and a["velocity_kts"] is not None
        and a["baro_altitude_ft"] < 10000 and a["velocity_kts"] > 250
    ]

    # Stale position fixes
    stale = [
        a for a in airborne
        if a.get("position_age_s") is not None and a["position_age_s"] > 60
    ]

    conn.close()

    return {
        "snapshot_id":    snapshot_id,
        "fetched_at":     snap["fetched_at"],
        "region":         snap["region"],
        "total_tracked":  snap["aircraft_count"],
        "airborne_count": len(airborne),
        "ground_count":   snap["aircraft_count"] - len(airborne),
        "enriched":       enriched,
        "emergency_squawks": [
            {
                "callsign":     e["callsign"],
                "icao24":       e["icao24"],
                "squawk":       e["squawk"],
                "altitude_ft":  e["baro_altitude_ft"],
                "speed_kts":    e["velocity_kts"],
                "registration": e.get("registration"),
                "model":        e.get("model"),
                "operator":     e.get("operator"),
            }
            for e in emergencies
        ],
        "altitude_stats":      alt_stats,
        "speed_stats":         spd_stats,
        "vertical_rate_stats": vr_stats,
        "top_countries":  [{"country": c, "count": n} for c, n in top_countries],
        "top_types":      [{"typecode": t, "count": n} for t, n in top_types],
        "top_operators":  [{"operator": o, "count": n} for o, n in top_operators],
        "low_fast_aircraft": [
            {
                "callsign":     a["callsign"],
                "icao24":       a["icao24"],
                "altitude_ft":  a["baro_altitude_ft"],
                "speed_kts":    a["velocity_kts"],
                "origin_country": a["origin_country"],
                "registration": a.get("registration"),
                "model":        a.get("model"),
                "operator":     a.get("operator"),
            }
            for a in low_fast[:10]
        ],
        "stale_position_count": len(stale),
    }


# ── Trend helpers ─────────────────────────────────────────────────────────────

def get_previous_snapshot_summary(snapshot_id: int) -> dict | None:
    """Return the summary for the snapshot immediately before the given ID."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1",
        (snapshot_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return get_snapshot_summary(row["id"])


# ── Utility ───────────────────────────────────────────────────────────────────

def list_snapshots(limit: int = 10) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, fetched_at, region, aircraft_count
        FROM snapshots ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


init_db()
