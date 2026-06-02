"""
tools/fetcher.py — OpenSky Network REST client.

Follows skills/SKILL.md:
  - Anonymous use, no auth required
  - 1 request per 10 seconds rate limit (we wait 12s between retries)
  - Bounding box params from SKILL.md
  - Null handling on all fields
"""

import time
import httpx
from tools.db import ingest_snapshot, REGIONS

OPENSKY_URL = "https://opensky-network.org/api/states/all"
RETRY_WAIT  = 12   # SKILL.md: stay clear of the 10s anonymous rate limit
TIMEOUT     = 20


def fetch_and_store(region: str = "US_WEST_COAST", retries: int = 3) -> tuple[int, int]:
    """
    Fetch live state vectors from OpenSky and write to SQLite.
    Returns (snapshot_id, aircraft_count).
    No LLM involved — deterministic ingest only.
    """
    bbox = REGIONS.get(region)
    if not bbox:
        raise ValueError(f"Unknown region: {region}. Available: {list(REGIONS.keys())}")

    print(f"[fetcher] Fetching {region} airspace from OpenSky Network...")

    last_err = None
    for attempt in range(retries):
        try:
            r = httpx.get(OPENSKY_URL, params=bbox, timeout=TIMEOUT)

            if r.status_code == 200:
                data = r.json()
                count = len(data.get("states") or [])
                print(f"[fetcher] Received {count} state vectors.")
                sid = ingest_snapshot(data, region=region)
                print(f"[fetcher] ✓ Stored snapshot_id={sid}")
                return sid, count

            elif r.status_code == 429:
                print(f"[fetcher] Rate limited (429). Waiting {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)

            elif r.status_code == 503:
                print(f"[fetcher] OpenSky unavailable (503). Waiting {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)

            else:
                last_err = f"HTTP {r.status_code}"
                print(f"[fetcher] Unexpected status: {r.status_code}")
                break

        except httpx.ConnectError as e:
            last_err = str(e)
            print(f"[fetcher] Connection error (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_WAIT)

        except httpx.TimeoutException:
            last_err = "Timeout"
            print(f"[fetcher] Request timed out (attempt {attempt+1}/{retries})")
            if attempt < retries - 1:
                time.sleep(RETRY_WAIT)

    raise RuntimeError(
        f"[fetcher] Failed to fetch OpenSky data after {retries} attempts. "
        f"Last error: {last_err}\n"
        "Check your internet connection or try again in a minute."
    )
