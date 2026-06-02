"""
orchestrator.py — Airspace Snapshot Analyst

Usage:
  python orchestrator.py snapshot            # fetch live data + generate report
  python orchestrator.py fetch               # fetch and store only (no report)
  python orchestrator.py report --id <id>    # generate report from stored snapshot
  python orchestrator.py list                # list stored snapshots
  python orchestrator.py help
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def cmd_snapshot():
    """Full pipeline: fetch → analyse → report."""
    from tools.fetcher import fetch_and_store
    from agents.analyst import run as analyse
    from agents.report_writer import run as write_report

    snapshot_id, count = fetch_and_store(region="US_WEST_COAST")
    print(f"\n[orchestrator] Snapshot {snapshot_id} stored ({count} aircraft).")

    analysis = analyse(snapshot_id)
    out_path = write_report(analysis)
    print(f"\n[orchestrator] ✓ Done. Report: {out_path}")


def cmd_fetch():
    """Fetch and store a snapshot only — no LLM, no report."""
    from tools.fetcher import fetch_and_store
    snapshot_id, count = fetch_and_store(region="US_WEST_COAST")
    print(f"\n[orchestrator] ✓ Snapshot {snapshot_id} stored ({count} aircraft).")
    print(f"[orchestrator] Run:  python orchestrator.py report --id {snapshot_id}")


def cmd_report(snapshot_id: int):
    """Generate a report from an already-stored snapshot."""
    from agents.analyst import run as analyse
    from agents.report_writer import run as write_report

    analysis = analyse(snapshot_id)
    out_path = write_report(analysis)
    print(f"\n[orchestrator] ✓ Report: {out_path}")


def cmd_list():
    from tools.db import list_snapshots
    snapshots = list_snapshots()
    if not snapshots:
        print("[orchestrator] No snapshots stored yet.")
        return
    print(f"\n{'─'*65}")
    print(f"{'ID':<6} {'Aircraft':<10} {'Region':<16} Fetched (UTC)")
    print(f"{'─'*65}")
    for s in snapshots:
        print(f"{s['id']:<6} {s['aircraft_count']:<10} {s['region']:<16} {s['fetched_at'][:19]}")
    print(f"{'─'*65}\n")


def cmd_help():
    print(__doc__)


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "help":
        cmd_help()

    elif args[0] == "snapshot":
        cmd_snapshot()

    elif args[0] == "fetch":
        cmd_fetch()

    elif args[0] == "report":
        if len(args) >= 3 and args[1] == "--id":
            try:
                cmd_report(int(args[2]))
            except ValueError:
                print(f"[orchestrator] snapshot id must be an integer, got: {args[2]}")
                sys.exit(1)
        else:
            print("Usage: python orchestrator.py report --id <snapshot_id>")
            sys.exit(1)

    elif args[0] == "list":
        cmd_list()

    else:
        print(f"Unknown command: {args[0]}")
        cmd_help()
