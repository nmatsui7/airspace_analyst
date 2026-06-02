"""
agents/trend_analyst.py — Agent 3

Compares the current snapshot with the previous one to identify meaningful
changes in traffic patterns, anomalies, and overall airspace behaviour.

Requires at least 2 snapshots in the DB. If only one exists, returns a
no-trend response gracefully.
"""

from pathlib import Path
from tools.db import get_snapshot_summary, get_previous_snapshot_summary
from tools.llm import call_llm_json

PROMPTS_DIR      = Path(__file__).parent.parent / "prompts"
TREND_SYSTEM     = (PROMPTS_DIR / "trend_analyst_system.txt").read_text()

SQUAWK_MEANINGS = {
    "7500": "Hijacking",
    "7600": "Radio Failure",
    "7700": "General Emergency",
}


def _format_comparison_for_llm(current: dict, previous: dict) -> str:
    """Build a comparison prompt from two snapshot summaries."""
    c_alt = current["altitude_stats"]
    c_spd = current["speed_stats"]
    c_vr  = current["vertical_rate_stats"]
    p_alt = previous["altitude_stats"]
    p_spd = previous["speed_stats"]
    p_vr  = previous["vertical_rate_stats"]

    lines = [
        "=== CURRENT SNAPSHOT ===",
        f"Snapshot ID:      {current['snapshot_id']}",
        f"Fetched at (UTC): {current['fetched_at']}",
        f"",
        f"  Total tracked:  {current['total_tracked']}",
        f"  Airborne:       {current['airborne_count']}",
        f"  On ground:      {current['ground_count']}",
        f"",
        f"  Below 10,000ft: {c_alt['low_count']} aircraft",
        f"  Above 600 kts:  {c_spd['fast_count']} aircraft",
        f"  Below 50 kts:   {c_spd['slow_count']} aircraft",
        f"  Steep climbs:   {c_vr['steep_climb_count']}",
        f"  Steep descents: {c_vr['steep_descent_count']}",
        f"",
        f"  Emergencies:",
    ]
    if current["emergency_squawks"]:
        for e in current["emergency_squawks"]:
            lines.append(
                f"    {e['squawk']} ({SQUAWK_MEANINGS.get(e['squawk'], '?')}) "
                f"— {e['callsign'] or e['icao24']}"
            )
    else:
        lines.append("    None")

    lines += [
        f"",
        f"  Low-fast aircraft: {len(current['low_fast_aircraft'])}",
        f"  Stale positions:   {current['stale_position_count']}",
        f"",
        f"=== PREVIOUS SNAPSHOT ({previous['snapshot_id']}) ===",
        f"Fetched at (UTC): {previous['fetched_at']}",
        f"",
        f"  Total tracked:  {previous['total_tracked']}",
        f"  Airborne:       {previous['airborne_count']}",
        f"  On ground:      {previous['ground_count']}",
        f"",
        f"  Below 10,000ft: {p_alt['low_count']} aircraft",
        f"  Above 600 kts:  {p_spd['fast_count']} aircraft",
        f"  Below 50 kts:   {p_spd['slow_count']} aircraft",
        f"  Steep climbs:   {p_vr['steep_climb_count']}",
        f"  Steep descents: {p_vr['steep_descent_count']}",
        f"",
        f"  Emergencies:",
    ]
    if previous["emergency_squawks"]:
        for e in previous["emergency_squawks"]:
            lines.append(
                f"    {e['squawk']} ({SQUAWK_MEANINGS.get(e['squawk'], '?')}) "
                f"— {e['callsign'] or e['icao24']}"
            )
    else:
        lines.append("    None")

    lines += [
        f"",
        f"  Low-fast aircraft: {len(previous['low_fast_aircraft'])}",
        f"  Stale positions:   {previous['stale_position_count']}",
    ]

    return "\n".join(lines)


def run(snapshot_id: int) -> dict:
    print(f"[trend_analyst] Loading snapshot {snapshot_id} and previous...")

    current  = get_snapshot_summary(snapshot_id)
    previous = get_previous_snapshot_summary(snapshot_id)

    if previous is None:
        print("[trend_analyst] Only one snapshot available — skipping trend analysis.")
        return {
            "has_trend": False,
            "current_snapshot_id": snapshot_id,
            "previous_snapshot_id": None,
            "traffic_delta": "Only one snapshot available. No trend comparison possible.",
            "anomaly_comparison": "",
            "trend_observations": [],
            "trend_narrative": "",
        }

    print(f"[trend_analyst] Comparing snapshot {snapshot_id} with #{previous['snapshot_id']}...")

    user_prompt = _format_comparison_for_llm(current, previous)

    print("[trend_analyst] Sending to Gemma 4 4B for trend analysis...")
    analysis = call_llm_json(TREND_SYSTEM, user_prompt)

    if not isinstance(analysis, dict) or not analysis:
        raise RuntimeError(
            "[trend_analyst] LLM returned empty or unparseable response."
        )

    # Normalize array fields
    val = analysis.get("trend_observations")
    if isinstance(val, str):
        analysis["trend_observations"] = [val] if val.strip() else []
    elif not isinstance(val, list):
        analysis["trend_observations"] = []

    # Attach snapshot timestamps for deterministic rendering in the report
    analysis["current_fetched_at"]  = current["fetched_at"]
    analysis["previous_fetched_at"] = previous["fetched_at"]

    print(f"[trend_analyst] ✓ Trend: {len(analysis.get('trend_observations', []))} observations")
    return analysis
