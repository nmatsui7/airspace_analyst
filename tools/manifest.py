"""
tools/manifest.py — List airborne aircraft with positions.

Usage:
  python -m tools.manifest                        # latest snapshot, text table
  python -m tools.manifest --id 2                 # specific snapshot
  python -m tools.manifest --html                 # HTML table output
  python -m tools.manifest --json                 # JSON output
  python -m tools.manifest --callsign UAL         # filter by callsign prefix
  python -m tools.manifest --min-alt 30000        # only aircraft above 30,000 ft
  python -m tools.manifest --emergency            # only emergency squawks
"""

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB   = ROOT / "airspace.db"


def fetch(snapshot_id: int | None, callsign: str | None = None,
          min_alt: float | None = None, max_alt: float | None = None,
          emergency_only: bool = False) -> tuple[int, list[dict]]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    if snapshot_id is None:
        row = conn.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No snapshots found.")
        snapshot_id = row["id"]

    snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    enriched = conn.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0] > 0

    cols = """
        sv.icao24, sv.callsign, sv.origin_country,
        sv.longitude, sv.latitude,
        sv.baro_altitude_ft, sv.velocity_kts, sv.true_track,
        sv.vertical_rate_fpm, sv.squawk, sv.is_emergency
    """
    if enriched:
        cols += ", a.registration, a.typecode, a.operator, a.manufacturer"
        sql = f"SELECT {cols} FROM state_vectors sv LEFT JOIN aircraft a ON sv.icao24 = a.icao24"
    else:
        sql = f"SELECT {cols} FROM state_vectors sv"

    conditions = ["sv.snapshot_id = ?", "sv.on_ground = 0", "sv.latitude IS NOT NULL"]
    params: list = [snapshot_id]

    if callsign:
        conditions.append("sv.callsign LIKE ?")
        params.append(f"{callsign.upper()}%")
    if min_alt is not None:
        conditions.append("sv.baro_altitude_ft >= ?")
        params.append(min_alt)
    if max_alt is not None:
        conditions.append("sv.baro_altitude_ft <= ?")
        params.append(max_alt)
    if emergency_only:
        conditions.append("sv.is_emergency = 1")

    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY sv.baro_altitude_ft DESC NULLS LAST"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return snapshot_id, rows


def _lat_str(lat: float | None) -> str:
    if lat is None:
        return ""
    ns = "N" if lat >= 0 else "S"
    return f"{abs(lat):.4f}°{ns}"


def _lon_str(lon: float | None) -> str:
    if lon is None:
        return ""
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lon):.4f}°{ew}"


def print_table(rows: list[dict]):
    if not rows:
        print("[manifest] No aircraft match the criteria.")
        return

    headers = ["Callsign", "Reg", "Type", "Operator", "Alt (ft)", "Spd (kts)",
               "Hdg", "V/S (fpm)", "Position", "Country", "Squawk"]
    col_widths = [12, 8, 8, 22, 10, 10, 5, 9, 26, 16, 6]

    def fmt_row(r: dict) -> list[str]:
        call = r.get("callsign") or r.get("icao24", "")[:8]
        alt  = f"{r['baro_altitude_ft']:>6,.0f}" if r.get("baro_altitude_ft") is not None else "  N/A"
        spd  = f"{r['velocity_kts']:>3.0f}" if r.get("velocity_kts") is not None else "N/A"
        hdg  = f"{r['true_track']:>3.0f}" if r.get("true_track") is not None else ""
        vr   = f"{r['vertical_rate_fpm']:>+5.0f}" if r.get("vertical_rate_fpm") is not None else "   N/A"
        pos  = f"{_lat_str(r['latitude'])} {_lon_str(r['longitude'])}"
        sq   = r.get("squawk") or ""
        if r.get("is_emergency"):
            sq = f"⚠ {sq}"
        return [
            call[:col_widths[0]],
            (r.get("registration") or "")[:col_widths[1]],
            (r.get("typecode") or "")[:col_widths[2]],
            (r.get("operator") or "")[:col_widths[3]],
            alt,
            spd,
            hdg,
            vr,
            pos,
            (r.get("origin_country") or "")[:col_widths[9]],
            sq,
        ]

    sep = "-" * (sum(col_widths) + len(col_widths) + 1)
    print(sep)
    print(" | ".join(h.ljust(w) for h, w in zip(headers, col_widths)))
    print(sep)
    for r in rows:
        cells = fmt_row(r)
        print(" | ".join(c.ljust(w) for c, w in zip(cells, col_widths)))
    print(sep)
    print(f"[manifest] {len(rows)} airborne aircraft")


def print_html(rows: list[dict], snapshot_id: int, output: str | None = None) -> Path:
    if not rows:
        raise RuntimeError("No aircraft match the criteria.")

    out_path = Path(output) if output else ROOT / "reports" / f"manifest_snapshot_{snapshot_id}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows_sorted = sorted(rows, key=lambda r: r.get("baro_altitude_ft") or 0, reverse=True)

    rows_html = ""
    for r in rows_sorted:
        alt = r.get("baro_altitude_ft")
        alt_str = f"{alt:,.0f}" if alt is not None else ""
        spd = r.get("velocity_kts")
        spd_str = f"{spd:.0f}" if spd is not None else ""
        hdg = r.get("true_track")
        hdg_str = f"{hdg:.0f}°" if hdg is not None else ""
        vr = r.get("vertical_rate_fpm")
        vr_str = f"{vr:+,.0f}" if vr is not None else ""
        lat = r.get("latitude")
        lon = r.get("longitude")
        pos_str = f"{_lat_str(lat)} {_lon_str(lon)}" if lat is not None else ""
        sq = r.get("squawk") or ""
        emergency = r.get("is_emergency")
        callsign = r.get("callsign") or r.get("icao24", "")
        reg = r.get("registration") or ""
        typecode = r.get("typecode") or ""
        operator = r.get("operator") or ""
        country = r.get("origin_country") or ""

        map_link = f"https://www.google.com/maps?q={lat},{lon}" if lat is not None and lon is not None else ""

        row_class = "emergency" if emergency else ""
        rows_html += f"""
        <tr class="{row_class}">
            <td class="callsign">{callsign}</td>
            <td>{reg}</td>
            <td>{typecode}</td>
            <td class="operator">{operator}</td>
            <td class="num">{alt_str}</td>
            <td class="num">{spd_str}</td>
            <td class="num">{hdg_str}</td>
            <td class="num">{vr_str}</td>
            <td class="pos">{pos_str}</td>
            <td>{country}</td>
            <td class="squawk">{'⚠ ' if emergency else ''}{sq}</td>
            <td class="map-link">{"<a href='" + map_link + "' target='_blank'>map</a>" if map_link else ""}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Airspace Manifest — Snapshot {snapshot_id}</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
         background:#f5f6fa; color:#333; padding:20px; }}
  h1 {{ font-size:1.3rem; margin-bottom:4px; }}
  .subtitle {{ color:#666; font-size:.85rem; margin-bottom:16px; }}
  .table-wrap {{ overflow-x:auto; background:#fff; border-radius:8px;
                box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  table {{ width:100%; border-collapse:collapse; font-size:.8rem; white-space:nowrap; }}
  th {{ background:#2c3e50; color:#fff; padding:10px 8px; text-align:left;
        font-weight:600; position:sticky; top:0; }}
  td {{ padding:6px 8px; border-bottom:1px solid #eee; }}
  tr:hover {{ background:#f0f4ff; }}
  tr.emergency {{ background:#fff0f0; }}
  tr.emergency:hover {{ background:#ffe0e0; }}
  .callsign {{ font-weight:600; color:#1a5276; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .pos {{ font-family:'SF Mono','Cascadia Code','Courier New',monospace; font-size:.75rem; color:#555; }}
  .operator {{ color:#555; }}
  .squawk {{ font-weight:600; }}
  .map-link a {{ color:#2980b9; text-decoration:none; font-size:.75rem; }}
  .map-link a:hover {{ text-decoration:underline; }}
  .count {{ margin-top:8px; font-size:.85rem; color:#666; }}
</style>
</head>
<body>
<h1>Airspace Manifest — Snapshot {snapshot_id}</h1>
<p class="subtitle">{snapshot_id} · {len(rows_sorted)} airborne aircraft</p>
<div class="table-wrap">
<table>
<thead>
<tr>
  <th>Callsign</th><th>Reg</th><th>Type</th><th>Operator</th>
  <th>Alt (ft)</th><th>Spd (kts)</th><th>Hdg</th><th>V/S (fpm)</th>
  <th>Position</th><th>Country</th><th>Squawk</th><th></th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
<p class="count">{len(rows_sorted)} aircraft</p>
</body>
</html>"""

    out_path.write_text(html)
    print(f"[manifest] HTML written: {out_path}")
    print(f"[manifest] {len(rows_sorted)} aircraft")
    return out_path


def print_json(rows: list[dict], snapshot_id: int):
    data = {
        "snapshot_id": snapshot_id,
        "aircraft_count": len(rows),
        "aircraft": rows,
    }
    # Remove None for cleaner JSON
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(val) for k, val in v.items() if val is not None}
        return v
    data["aircraft"] = [clean(r) for r in rows]
    print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="List airborne aircraft with positions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--id", type=int, help="Snapshot ID (default: latest)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--html", type=str, nargs="?", const="",
                        help="Output as HTML file (optional path)")
    parser.add_argument("--callsign", type=str, help="Filter by callsign prefix")
    parser.add_argument("--min-alt", type=float, help="Minimum altitude (ft)")
    parser.add_argument("--max-alt", type=float, help="Maximum altitude (ft)")
    parser.add_argument("--emergency", action="store_true", help="Emergency squawks only")
    args = parser.parse_args()

    sid, rows = fetch(snapshot_id=args.id, callsign=args.callsign,
                      min_alt=args.min_alt, max_alt=args.max_alt,
                      emergency_only=args.emergency)

    if args.json:
        print_json(rows, sid)
    elif args.html is not None:
        output = args.html if args.html else None
        print_html(rows, sid, output=output)
    else:
        print(f"[manifest] Snapshot {sid}")
        print_table(rows)
