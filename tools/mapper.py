"""
tools/mapper.py — Generate an interactive HTML map of aircraft positions.

Usage:
  python -m tools.mapper                                    # all aircraft, latest snapshot
  python -m tools.mapper --id 2                             # all aircraft, snapshot 2
  python -m tools.mapper --callsign UAL949                  # track one aircraft by callsign
  python -m tools.mapper --icao a7250f                      # track one aircraft by hex code
  python -m tools.mapper --registration N56FA               # track one aircraft by tail number
  python -m tools.mapper --callsign UAL949 --id 2           # one aircraft, single snapshot
  python -m tools.mapper --callsign DAL728 --output trail   # trajectory across all snapshots
"""

import argparse
import re
import sqlite3
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

ROOT   = Path(__file__).parent.parent
DB     = ROOT / "airspace.db"
OUT    = ROOT / "reports" / "aircraft_map.html"

CENTRE = [37.5, -120.0]
ZOOM   = 6

ALT_COLOURS = [
    (0,      10000,  "green"),
    (10000,  30000,  "orange"),
    (30000,  999999, "blue"),
]


def _alt_colour(ft: float | None) -> str:
    if ft is None:
        return "gray"
    for lo, hi, colour in ALT_COLOURS:
        if lo <= ft < hi:
            return colour
    return "gray"


def _popup_text(row: dict) -> str:
    parts = [
        f"<b>{row.get('callsign') or row.get('icao24', '?')}</b>",
    ]
    if row.get("registration"):
        parts.append(f"Reg: {row['registration']}")
    if row.get("typecode"):
        parts.append(f"Type: {row['typecode']}")
    if row.get("operator"):
        parts.append(f"Op: {row['operator']}")
    if row.get("manufacturer"):
        parts.append(f"Mfr: {row['manufacturer']}")
    if row.get("origin_country"):
        parts.append(f"Country: {row['origin_country']}")
    if row.get("snapshot_label"):
        parts.append(f"<i>{row['snapshot_label']}</i>")
    alt = row.get("baro_altitude_ft")
    if alt is not None:
        parts.append(f"Alt: {alt:,.0f} ft")
    spd = row.get("velocity_kts")
    if spd is not None:
        parts.append(f"Speed: {spd:.0f} kts")
    vr = row.get("vertical_rate_fpm")
    if vr is not None:
        parts.append(f"V/S: {vr:+.0f} fpm")
    hdg = row.get("true_track")
    if hdg is not None:
        parts.append(f"Heading: {hdg:.0f}°")
    if row.get("squawk"):
        parts.append(f"Squawk: <b>{row['squawk']}</b>")
    if row.get("on_ground"):
        parts.append("<i>ON GROUND</i>")
    return "<br>".join(parts)


def _marker_radius(row: dict) -> int:
    spd = row.get("velocity_kts")
    if spd is None:
        return 4
    return min(max(int(spd / 40), 3), 12)


def _enriched_join():
    return """
        SELECT sv.*,
               a.registration, a.typecode, a.operator,
               a.operator_icao, a.manufacturer
        FROM state_vectors sv
        LEFT JOIN aircraft a ON sv.icao24 = a.icao24
    """


def _search_filter(conn, icao: str | None, callsign: str | None,
                   registration: str | None) -> list[dict]:
    """Search for an aircraft by icao24, callsign, or tail number."""
    enriched = conn.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0] > 0
    join_sql = _enriched_join() if enriched else "SELECT sv.* FROM state_vectors sv"

    if icao:
        rows = conn.execute(f"{join_sql} WHERE sv.icao24 = ?", (icao.strip().lower(),)).fetchall()
    elif callsign:
        rows = conn.execute(f"{join_sql} WHERE sv.callsign = ? COLLATE NOCASE",
                            (callsign.strip().upper(),)).fetchall()
    elif registration:
        reg = registration.strip().upper()
        if enriched:
            rows = conn.execute(f"""
                {join_sql} WHERE a.registration = ? COLLATE NOCASE
            """, (reg,)).fetchall()
        else:
            print("[mapper] Aircraft DB not loaded — cannot search by registration.")
            return []
    else:
        return []

    return [dict(r) for r in rows]


def _popup_html(row: dict) -> str:
    """Compact one-line popup for individual aircraft tracks."""
    parts = [f"<b>{row.get('callsign') or row.get('icao24', '?')}</b>"]
    alt = row.get("baro_altitude_ft")
    if alt is not None:
        parts.append(f"{alt:,.0f} ft")
    spd = row.get("velocity_kts")
    if spd is not None:
        parts.append(f"{spd:.0f} kts")
    if row.get("squawk"):
        parts.append(f"<span style='color:red'>SQ {row['squawk']}</span>")
    if row.get("snapshot_label"):
        parts.append(f"<br><i>{row['snapshot_label']}</i>")
    return " · ".join(parts)


def generate(snapshot_id: int | None = None, output: str | Path | None = None,
             icao: str | None = None, callsign: str | None = None,
             registration: str | None = None) -> Path:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    enriched = conn.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0] > 0

    # ── Filter mode: single aircraft ──────────────────────────────────────
    if icao or callsign or registration:
        rows = _search_filter(conn, icao, callsign, registration)
        if not rows:
            raise RuntimeError(f"No aircraft found matching the given criteria.")

        # If a specific snapshot is requested, filter to it
        if snapshot_id is not None:
            rows = [r for r in rows if r["snapshot_id"] == snapshot_id]

        if not rows:
            raise RuntimeError("No positions found for the given filter.")

        label = (f"{rows[0].get('callsign') or rows[0].get('icao24', '?')}"
                 f"{' (' + rows[0].get('registration', '') + ')' if rows[0].get('registration') else ''}")
        icao24 = rows[0]["icao24"]

        # Sort by snapshot time for trajectory
        snap_times = {
            r["id"]: r["fetched_at"]
            for r in conn.execute("SELECT id, fetched_at FROM snapshots").fetchall()
        }
        for r in rows:
            ts = snap_times.get(r["snapshot_id"], "")
            r["snapshot_label"] = ts[:19] if ts else f"snapshot {r['snapshot_id']}"

        rows.sort(key=lambda r: snap_times.get(r["snapshot_id"], ""))

        # Build trajectory line
        trajectory = [[r["latitude"], r["longitude"]] for r in rows
                      if r["latitude"] is not None and r["longitude"] is not None]

        m = folium.Map(location=trajectory[0] if trajectory else CENTRE,
                       zoom_start=8 if len(trajectory) > 1 else ZOOM,
                       tiles="CartoDB Positron",
                       attr="© OpenStreetMap contributors, © CartoDB")

        # Title
        title_html = f"""
        <div style="position:fixed; top:10px; left:50%; transform:translateX(-50%); z-index:9999;
                    background:white; padding:8px 20px; border-radius:8px;
                    box-shadow:0 0 8px rgba(0,0,0,.2); font:14px sans-serif;
                    text-align:center">
            <b>{label}</b> · {len(trajectory)} position{'' if len(trajectory) == 1 else 's'}
            · ICAO: {icao24}
        </div>
        """
        m.get_root().html.add_child(folium.Element(title_html))

        # Draw trail if multiple positions
        if len(trajectory) >= 2:
            folium.PolyLine(
                trajectory,
                color="#333",
                weight=3,
                opacity=0.6,
                popup=f"{label} trajectory ({len(trajectory)} points)",
            ).add_to(m)

        # Markers per position
        n = len(rows)
        for i, row in enumerate(rows):
            lat, lon = row.get("latitude"), row.get("longitude")
            if lat is None or lon is None:
                continue

            # Progress gradient: first=light, last=dark
            t = i / max(n - 1, 1)
            r_val = int(50 + 150 * t)
            colour = f"#{r_val:02x}{int(50 + 80 * (1 - t)):02x}50"

            popup = folium.Popup(_popup_html(row), max_width=300)

            if row.get("is_emergency"):
                folium.Marker(
                    [lat, lon],
                    popup=popup,
                    icon=folium.Icon(color="red", icon="warning-sign", prefix="glyphicon"),
                ).add_to(m)
            else:
                folium.CircleMarker(
                    [lat, lon],
                    radius=8 if i == n - 1 else 6,
                    color=colour,
                    fill=True,
                    fill_color=colour,
                    fill_opacity=0.8,
                    weight=2,
                    popup=popup,
                    tooltip=f"{label} · {row['snapshot_label']}",
                ).add_to(m)

                # Start marker
                if i == 0 and n > 1:
                    folium.Marker(
                        [lat, lon],
                        popup=f"Start: {row['snapshot_label']}",
                        icon=folium.Icon(color="green", icon="play", prefix="glyphicon"),
                    ).add_to(m)
                # End marker
                if i == n - 1 and n > 1:
                    folium.Marker(
                        [lat, lon],
                        popup=f"Current: {row['snapshot_label']}",
                        icon=folium.Icon(color="blue", icon="ok", prefix="glyphicon"),
                    ).add_to(m)

        out_path = Path(output) if output else ROOT / "reports" / f"aircraft_{icao24}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        m.save(str(out_path))

        print(f"[mapper] Track map: {out_path}")
        print(f"[mapper] {label} ({icao24}) — {len(rows)} position{'' if len(rows) == 1 else 's'}")
        conn.close()
        return out_path

    # ── Full-snapshot mode (unchanged) ────────────────────────────────────
    if snapshot_id is None:
        row = conn.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No snapshots found. Run fetch first.")
        snapshot_id = row["id"]

    if enriched:
        rows = conn.execute(f"""
            {_enriched_join()}
            WHERE sv.snapshot_id = ?
        """, (snapshot_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM state_vectors
            WHERE snapshot_id = ?
        """, (snapshot_id,)).fetchall()

    snap = dict(conn.execute(
        "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone())
    conn.close()

    m = folium.Map(location=CENTRE, zoom_start=ZOOM,
                   tiles="CartoDB Positron",
                   attr="© OpenStreetMap contributors, © CartoDB")

    legend_html = """
    <div style="position:fixed; bottom:20px; left:20px; z-index:9999;
                background:white; padding:12px 16px; border-radius:8px;
                box-shadow:0 0 8px rgba(0,0,0,.2); font:13px sans-serif;
                line-height:1.6">
        <b>Altitude</b><br>
        <span style="color:green">●</span> &lt; 10,000 ft<br>
        <span style="color:orange">●</span> 10,000 – 30,000 ft<br>
        <span style="color:blue">●</span> &gt; 30,000 ft<br>
        <span style="color:red">◆</span> Emergency squawk<br>
        <span style="color:gray">●</span> On ground
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    emergencies = folium.FeatureGroup(name="Emergencies")
    airborne    = folium.FeatureGroup(name="Airborne")
    grounded    = folium.FeatureGroup(name="On Ground")
    cluster     = MarkerCluster().add_to(m)

    for r in rows:
        row = dict(r)
        lat, lon = row.get("latitude"), row.get("longitude")
        if lat is None or lon is None:
            continue

        popup   = folium.Popup(_popup_text(row), max_width=350)
        colour  = "red" if row.get("is_emergency") else _alt_colour(row.get("baro_altitude_ft"))
        radius  = _marker_radius(row)

        if row.get("is_emergency"):
            folium.Marker(
                [lat, lon], popup=popup,
                icon=folium.Icon(color="red", icon="warning-sign", prefix="glyphicon"),
            ).add_to(emergencies)
        elif row.get("on_ground"):
            folium.CircleMarker(
                [lat, lon], radius=3, color="gray",
                fill=True, fill_opacity=0.5, popup=popup,
            ).add_to(grounded)
        else:
            folium.CircleMarker(
                [lat, lon], radius=radius, color=colour,
                fill=True, fill_opacity=0.6, weight=1.5, popup=popup,
            ).add_to(airborne)

    for fg in (emergencies, airborne, grounded):
        fg.add_to(cluster)
    folium.LayerControl().add_to(m)

    out_path = Path(output) if output else OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))

    print(f"[mapper] Map written: {out_path}")
    print(f"[mapper] Snapshot {snap['id']} — {snap['fetched_at'][:19]} UTC  "
          f"({len(rows)} aircraft)")
    return out_path


def _resolve_callsigns(conn, pattern: str, limit: int = 20) -> list[tuple]:
    """Search callsigns matching a pattern (for tab completion / exploration)."""
    enriched = conn.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0] > 0
    results = []

    if enriched:
        results += conn.execute("""
            SELECT DISTINCT sv.callsign, a.registration, a.typecode, a.operator
            FROM state_vectors sv
            LEFT JOIN aircraft a ON sv.icao24 = a.icao24
            WHERE sv.callsign LIKE ? AND sv.callsign != ''
            ORDER BY sv.callsign
            LIMIT ?
        """, (f"%{pattern.upper()}%", limit)).fetchall()

    results += conn.execute("""
        SELECT DISTINCT callsign, NULL, NULL, NULL
        FROM state_vectors
        WHERE callsign LIKE ? AND callsign != ''
          AND callsign NOT IN (
              SELECT callsign FROM state_vectors sv
              LEFT JOIN aircraft a ON sv.icao24 = a.icao24
              WHERE a.registration IS NOT NULL
          )
        ORDER BY callsign
        LIMIT ?
    """, (f"%{pattern.upper()}%", limit)).fetchall()

    return [dict(r) for r in results[:limit]]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate aircraft position map",
        epilog="Examples:\n"
               "  %(prog)s                          all aircraft, latest snapshot\n"
               "  %(prog)s --id 2                   all aircraft, snapshot 2\n"
               "  %(prog)s --callsign UAL949        track one aircraft by callsign\n"
               "  %(prog)s --icao a7250f            track one aircraft by hex code\n"
               "  %(prog)s --registration N56FA     track one aircraft by tail number\n"
               "  %(prog)s --callsign DAL --find     search for matching callsigns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--id", type=int, help="Snapshot ID (default: latest)")
    parser.add_argument("--output", type=str, help="Output HTML path")
    parser.add_argument("--icao", type=str, help="Filter by ICAO24 hex code")
    parser.add_argument("--callsign", type=str, help="Filter by callsign")
    parser.add_argument("--registration", type=str, help="Filter by tail number")
    parser.add_argument("--find", action="store_true",
                        help="Search matching callsigns (use with --callsign)")
    args = parser.parse_args()

    if args.find and args.callsign:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        matches = _resolve_callsigns(conn, args.callsign)
        conn.close()
        if matches:
            print(f"[mapper] Matching callsigns ({len(matches)}):")
            for m in matches:
                extras = []
                if m.get("registration"):
                    extras.append(m["registration"])
                if m.get("typecode"):
                    extras.append(m["typecode"])
                if m.get("operator"):
                    extras.append(m["operator"])
                extra_str = f"  ({', '.join(extras)})" if extras else ""
                print(f"  {m['callsign']}{extra_str}")
        else:
            print("[mapper] No matching callsigns found.")
        raise SystemExit(0)

    generate(snapshot_id=args.id, output=args.output,
             icao=args.icao, callsign=args.callsign,
             registration=args.registration)
