"""
agents/report_writer.py — Agent 2

Receives structured analysis dict from Agent 1 (Analyst):
  1. Passes it to write_report.js via Node.js for the .docx report
  2. Generates an interactive HTML aircraft position map via mapper.py
  3. Generates an HTML manifest table via manifest.py
No LLM call here — purely deterministic document generation.
"""

import json
import subprocess
import sys
from pathlib import Path

WRITE_REPORT_JS = Path(__file__).parent.parent / "tools" / "write_report.js"
ROOT            = Path(__file__).parent.parent


def run(analysis: dict) -> Path:
    status = analysis.get("overall_status", "?")
    n_emergencies = len(analysis.get("emergency_alerts", []))
    n_anomalies   = len(analysis.get("anomalies", []))

    print(f"[report_writer] Building .docx  "
          f"status={status}  anomalies={n_anomalies}  emergencies={n_emergencies}")

    # Run trend analyst if a snapshot_id is available
    snapshot_id = analysis.get("_summary", {}).get("snapshot_id")
    if snapshot_id is not None:
        try:
            from agents.trend_analyst import run as trend_analyst
            analysis["trend_analysis"] = trend_analyst(snapshot_id)
        except Exception as e:
            print(f"[report_writer] Trend analysis skipped: {e}")
    else:
        print("[report_writer] No snapshot_id — skipping trend analysis.")

    result = subprocess.run(
        ["node", str(WRITE_REPORT_JS)],
        input=json.dumps(analysis, ensure_ascii=False),
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        print(f"[report_writer] ERROR:\n{result.stderr}")
        sys.exit(1)

    output_line = result.stdout.strip()
    print(output_line)

    if "Written:" in output_line:
        docx_path = Path(output_line.split("Written:")[-1].strip())
    else:
        docx_path = Path("reports/")

    report_dir = docx_path.parent
    snapshot_id = analysis.get("_summary", {}).get("snapshot_id")

    # Generate interactive aircraft position map
    if snapshot_id is not None:
        map_path = report_dir / f"aircraft_map_snapshot_{snapshot_id}.html"
        try:
            from tools.mapper import generate as generate_map
            generate_map(snapshot_id=snapshot_id, output=str(map_path))
        except Exception as e:
            print(f"[report_writer] Map generation skipped: {e}")

        # Generate aircraft manifest HTML table
        manifest_path = report_dir / f"manifest_snapshot_{snapshot_id}.html"
        try:
            from tools.manifest import print_html
            from tools.manifest import fetch as fetch_manifest
            _, rows = fetch_manifest(snapshot_id=snapshot_id)
            print_html(rows, snapshot_id, output=str(manifest_path))
        except Exception as e:
            print(f"[report_writer] Manifest generation skipped: {e}")
    else:
        print("[report_writer] No snapshot_id in analysis — skipping map and manifest.")

    return docx_path
