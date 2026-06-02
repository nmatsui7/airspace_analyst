"""
download_aircraft_db.py — Download the OpenSky aircraft database CSV.

Run once before your first snapshot:
  python download_aircraft_db.py

The CSV (~50MB) maps ICAO24 hex codes to:
  registration, model, typecode, operator, operator_icao, manufacturer, engines
"""

import sys
import httpx
from pathlib import Path

URL      = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
OUT_DIR  = Path(__file__).parent / "data"
OUT_FILE = OUT_DIR / "aircraftDatabase.csv"


def download():
    OUT_DIR.mkdir(exist_ok=True)

    if OUT_FILE.exists():
        size_mb = OUT_FILE.stat().st_size / 1_000_000
        print(f"[download] Aircraft DB already exists ({size_mb:.1f} MB): {OUT_FILE}")
        ans = input("[download] Re-download? (y/N): ").strip().lower()
        if ans != "y":
            print("[download] Skipped.")
            return

    print(f"[download] Downloading from {URL} ...")
    print("[download] This is ~50MB — may take 30–60 seconds on a normal connection.")

    with httpx.stream("GET", URL, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0

        with open(OUT_FILE, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r[download] {pct:.1f}%  ({downloaded/1e6:.1f} MB)", end="", flush=True)

    print(f"\n[download] ✓ Saved to {OUT_FILE} ({OUT_FILE.stat().st_size/1e6:.1f} MB)")

    # Load into SQLite
    print("[download] Loading into SQLite aircraft table...")
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from tools.db import load_aircraft_db
    n = load_aircraft_db(OUT_FILE)
    print(f"[download] ✓ {n:,} aircraft records ready for enrichment.")


if __name__ == "__main__":
    download()
