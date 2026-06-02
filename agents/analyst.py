"""
agents/analyst.py — Agent 1

Reads the pre-computed snapshot summary from SQLite (deterministic),
sends it to Gemma 4 4B (via llama.cpp) for pattern analysis and anomaly detection,
returns structured JSON for the Report Writer agent.

LLM call is the ONLY non-deterministic step in the pipeline.
"""

import json
from pathlib import Path
from tools.db import get_snapshot_summary
from tools.llm import call_llm_json

PROMPTS_DIR    = Path(__file__).parent.parent / "prompts"
ANALYST_SYSTEM = (PROMPTS_DIR / "analyst_system.txt").read_text()

SQUAWK_MEANINGS = {
    "7500": "Hijacking",
    "7600": "Radio Failure",
    "7700": "General Emergency",
}


def _format_summary_for_llm(summary: dict) -> str:
    """
    Serialise the snapshot summary into a clean user prompt.
    Numbers are pre-computed in db.py — LLM only reasons, never calculates.
    """
    alt  = summary["altitude_stats"]
    spd  = summary["speed_stats"]
    vr   = summary["vertical_rate_stats"]

    lines = [
        f"Snapshot ID:      {summary['snapshot_id']}",
        f"Fetched at (UTC): {summary['fetched_at']}",
        f"Region:           {summary['region']}",
        f"",
        f"TRAFFIC COUNTS",
        f"  Total tracked:  {summary['total_tracked']}",
        f"  Airborne:       {summary['airborne_count']}",
        f"  On ground:      {summary['ground_count']}",
        f"",
        f"ALTITUDE STATS (airborne aircraft)",
        f"  Min:            {alt['min_ft']} ft",
        f"  Max:            {alt['max_ft']} ft",
        f"  Average:        {alt['avg_ft']} ft",
        f"  Below 10,000ft: {alt['low_count']} aircraft  ← anomaly threshold",
        f"",
        f"SPEED STATS (airborne aircraft)",
        f"  Min:            {spd['min_kts']} kts",
        f"  Max:            {spd['max_kts']} kts",
        f"  Average:        {spd['avg_kts']} kts",
        f"  Above 600 kts:  {spd['fast_count']} aircraft  ← anomaly threshold",
        f"  Below 50 kts:   {spd['slow_count']} aircraft  ← anomaly threshold",
        f"",
        f"VERTICAL RATE ANOMALIES",
        f"  Steep climb  (>2000 fpm): {vr['steep_climb_count']} aircraft",
        f"  Steep descent(<-3000 fpm): {vr['steep_descent_count']} aircraft",
        f"",
        f"EMERGENCY SQUAWKS",
    ]

    if summary["emergency_squawks"]:
        for e in summary["emergency_squawks"]:
            lines.append(
                f"  *** {e['squawk']} ({SQUAWK_MEANINGS.get(e['squawk'], 'Unknown')}) "
                f"— {e['callsign'] or e['icao24']}  "
                f"alt={e['altitude_ft']} ft  spd={e['speed_kts']} kts ***"
            )
    else:
        lines.append("  None detected.")

    lines += [
        f"",
        f"LOW-ALTITUDE / HIGH-SPEED AIRCRAFT (below 10,000ft AND above 250 kts)",
    ]
    if summary["low_fast_aircraft"]:
        for a in summary["low_fast_aircraft"]:
            lines.append(
                f"  {a['callsign'] or a['icao24']}  "
                f"{a['altitude_ft']} ft  {a['speed_kts']} kts  "
                f"({a['origin_country']})"
            )
    else:
        lines.append("  None detected.")

    lines += [
        f"",
        f"TOP ORIGIN COUNTRIES",
    ]
    for entry in summary["top_countries"]:
        lines.append(f"  {entry['country']:<25} {entry['count']} aircraft")

    return "\n".join(lines)


def run(snapshot_id: int) -> dict:
    print(f"[analyst] Loading snapshot {snapshot_id} from DB...")
    summary = get_snapshot_summary(snapshot_id)

    print(f"[analyst] {summary['airborne_count']} airborne / "
          f"{summary['total_tracked']} total tracked.")

    if summary["emergency_squawks"]:
        print(f"[analyst] ⚠️  {len(summary['emergency_squawks'])} EMERGENCY SQUAWK(S) detected!")

    user_prompt = _format_summary_for_llm(summary)

    print("[analyst] Sending to Gemma 4 4B for analysis...")
    analysis = call_llm_json(ANALYST_SYSTEM, user_prompt)

    if not isinstance(analysis, dict) or not analysis:
        raise RuntimeError(
            "[analyst] LLM returned empty or unparseable response. "
            "Check llama-server is running (./run_gemma.sh)."
        )

    # Enrich emergency alerts with squawk meanings if LLM omitted them
    for alert in analysis.get("emergency_alerts", []):
        if not alert.get("meaning"):
            alert["meaning"] = SQUAWK_MEANINGS.get(alert.get("squawk", ""), "Unknown")

    # Normalize array fields — LLMs sometimes return strings instead of arrays
    for field in ("anomalies", "emergency_alerts", "notable_patterns"):
        val = analysis.get(field)
        if isinstance(val, str):
            analysis[field] = [val] if val.strip() else []
        elif not isinstance(val, list):
            analysis[field] = []

    analysis["_summary"] = summary

    print(f"[analyst] ✓ Status: {analysis.get('overall_status', '?')}  "
          f"| Anomalies: {len(analysis.get('anomalies', []))}  "
          f"| Emergencies: {len(analysis.get('emergency_alerts', []))}")

    return analysis
