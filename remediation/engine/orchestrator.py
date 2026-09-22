#!/usr/bin/env python3
"""
Netwroxia — Phase C15: Orchestrator CLI

Top-level command interface for Phase C. Chains C3 (incident engine)
and C14 (executor) into a single pipeline.

    tick        read RCA -> create/update incidents  (C3)
    run         execute ready incidents              (C14)
    cycle       tick + run
    approvals   list pending approvals               (C6)
    approve ID  approve then execute
    deny ID     deny a pending approval
    status      show incident + pending summary
    history ID  show one incident's full history
    watch       loop cycle forever

CLI:
    python3 remediation/engine/orchestrator.py cycle [--dry-run]
    python3 remediation/engine/orchestrator.py status
    python3 remediation/engine/orchestrator.py approvals
    python3 remediation/engine/orchestrator.py approve <incident_id>
    python3 remediation/engine/orchestrator.py watch --interval 30
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit import audit_logger as audit
from incidents import incident_engine, incident_store as store
from remediation.engine import executor
from remediation.guardrails import approval_gate

# ── CYCLE ───────────────────────────────────────────────────────────────────
def run_cycle(dry_run: bool = False) -> Dict[str, Any]:
    """
    Full pipeline: tick (C3) then run (C14). Returns a combined summary.
    """
    audit.log(
        event="orchestrator.cycle.started",
        actor="auto",
        data={"dry_run": dry_run},
    )

      # ── Phase 1: tick (create/update incidents from RCA) ──────────────
    # Note: even in dry-run mode, we PERSIST incidents. Recording an
    # incident is not a network action. Dry-run means "don't touch the
    # network" — the executor enforces that. If we skipped persistence,
    # the executor would have nothing to execute against.
    tick_summary: Dict[str, Any] = {}
    try:
        tick_summary = incident_engine.tick(dry_run=False)
    except Exception as e:
        tick_summary = {"error": f"{type(e).__name__}: {e}"}

    # ── Phase 2: run (execute ready incidents) ────────────────────────
    run_summary: Dict[str, Any] = {}
    try:
        run_summary = executor.run_all(dry_run=dry_run)
    except Exception as e:
        run_summary = {"error": f"{type(e).__name__}: {e}"}

    summary = {
        "dry_run": dry_run,
        "tick": tick_summary,
        "run": run_summary,
    }

    audit.log(
        event="orchestrator.cycle.completed",
        actor="auto",
        data={
            "dry_run": dry_run,
            "created": tick_summary.get("created", 0),
            "observed": tick_summary.get("observed", 0),
            "executed": run_summary.get("total", 0),
        },
    )
    return summary


# ── APPROVAL HELPERS ────────────────────────────────────────────────────────
def list_pending_approvals() -> List[Dict[str, Any]]:
    return approval_gate.list_by_decision("PENDING")


def approve_and_run(incident_id: str, approver: str,
                    note: str = "", dry_run: bool = False) -> Dict[str, Any]:
    """Approve a pending incident, then execute it immediately."""
    try:
        approval_gate.approve(incident_id, approver=approver, note=note)
    except ValueError as e:
        return {"ok": False, "stage": "approve", "error": str(e)}

    result = executor.execute_incident(incident_id, dry_run=dry_run)
    return result


def deny_approval(incident_id: str, approver: str,
                  note: str = "") -> Dict[str, Any]:
    try:
        entry = approval_gate.deny(incident_id, approver=approver, note=note)
        return {"ok": True, "entry": entry}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def _hr(char: str = "─", n: int = 78) -> str:
    return char * n


def print_status() -> None:
    counts = store.count_by_state()
    total = sum(counts.values())
    print(_hr("="))
    print(" NETWROXIA ORCHESTRATOR — STATUS")
    print(_hr("="))
    print(f" Total incidents : {total}")
    for state in sorted(counts):
        if counts[state] > 0:
            print(f"   {state:20s}  {counts[state]}")
    print(_hr())

    pending = list_pending_approvals()
    print(f" Pending approvals : {len(pending)}")
    for p in pending:
        print(f"   {p.get('incident_id', '?'):40s}  "
              f"action={p.get('action', '?'):18s}  "
              f"sev={p.get('severity', '?'):8s}  "
              f"requested_at={p.get('requested_at', '?')}")
    print(_hr("="))


def print_cycle_summary(summary: Dict[str, Any]) -> None:
    dry = summary.get("dry_run", False)
    print(_hr("="))
    print(f" NETWROXIA ORCHESTRATOR — CYCLE "
          f"({'DRY-RUN' if dry else 'LIVE'})")
    print(_hr("="))

    tick = summary.get("tick", {})
    print(" [TICK] C3 incident engine")
    for k in ("rca_incidents", "created", "observed", "advanced",
              "orphans_closed", "skipped_terminal"):
        if k in tick:
            print(f"   {k:20s}  {tick[k]}")
    if tick.get("error"):
        print(f"   ERROR: {tick['error']}")

    run = summary.get("run", {})
    print(" [RUN] C14 executor")
    print(f"   {'total':20s}  {run.get('total', 0)}")
    for entry in run.get("results", []):
        iid = entry.get("incident_id", "?")
        r = entry.get("result", {})
        stage = r.get("stage", "?")
        action = r.get("action", "-")
        ok = "OK" if r.get("ok") else "FAIL"
        print(f"   [{ok:4s}] {iid[:32]:32s}  "
              f"stage={stage:20s}  action={action}")
    if run.get("error"):
        print(f"   ERROR: {run['error']}")

    print(_hr("="))


def print_history(incident_id: str) -> int:
    inc = store.get_incident(incident_id)
    if inc is None:
        print(f"[MISS] {incident_id}")
        return 1

    print(_hr("="))
    print(f" INCIDENT {incident_id}")
    print(_hr("="))
    print(f" State     : {inc.get('state')}")
    print(f" Severity  : {inc.get('severity')}")
    print(f" Root      : {inc.get('root_router')}")
    print(f" Created   : {inc.get('created_at')}")
    print(f" Updated   : {inc.get('updated_at')}")
    print(f" Resolved  : {inc.get('resolved_at')}")
    print(_hr())

    transitions = store.get_transitions(incident_id)
    print(f" TRANSITIONS ({len(transitions)})")
    for t in transitions:
        print(f"   {t['ts']}  "
              f"{t.get('from_state') or '-':18s} -> {t['to_state']:18s}  "
              f"by {t['actor']:14s}  "
              f"{t.get('reason', '')[:40]}")
    print(_hr())

    events = [
        e for e in audit.tail(2000)
        if e.get("incident_id") == incident_id
    ]
    print(f" AUDIT EVENTS ({len(events)})")
    for e in events[-20:]:
        print(f"   {e['ts']}  {e['event']:32s}  "
              f"actor={e.get('actor', '?'):12s}  "
              f"{json.dumps(e.get('data', {}))[:80]}")
    print(_hr("="))
    return 0


# ── COMMANDS ────────────────────────────────────────────────────────────────
def cmd_tick(args) -> int:
    summary = incident_engine.tick(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_run(args) -> int:
    summary = executor.run_all(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


def cmd_cycle(args) -> int:
    summary = run_cycle(dry_run=args.dry_run)
    print_cycle_summary(summary)
    return 0


def cmd_approvals(_args) -> int:
    pending = list_pending_approvals()
    if not pending:
        print("(no pending approvals)")
        return 0
    print(f"[APPROVALS] {len(pending)} pending")
    for p in pending:
        print(f"  {p.get('incident_id', '?'):40s}  "
              f"action={p.get('action', '?'):20s}  "
              f"sev={p.get('severity', '?'):10s}  "
              f"requested_by={p.get('requested_by', '?')}")
    return 0


def cmd_approve(args) -> int:
    result = approve_and_run(
        incident_id=args.incident_id,
        approver=args.approver,
        note=args.note,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_deny(args) -> int:
    result = deny_approval(
        incident_id=args.incident_id,
        approver=args.approver,
        note=args.note,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_status(_args) -> int:
    print_status()
    return 0


def cmd_history(args) -> int:
    return print_history(args.incident_id)


def cmd_watch(args) -> int:
    print(f"[WATCH] interval={args.interval}s  dry_run={args.dry_run}")
    print("        Ctrl+C to stop")
    try:
        while True:
            summary = run_cycle(dry_run=args.dry_run)
            print_cycle_summary(summary)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[WATCH] stopped by user")
        return 0


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Orchestrator (C15)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_dryrun(p):
        p.add_argument("--dry-run", action="store_true",
                       help="Pass through to executor; no network touches")

    p_tick = sub.add_parser("tick", help="Run C3 incident engine")
    add_dryrun(p_tick)

    p_run = sub.add_parser("run", help="Run C14 executor")
    add_dryrun(p_run)

    p_cycle = sub.add_parser("cycle", help="tick + run")
    add_dryrun(p_cycle)

    sub.add_parser("approvals", help="List pending approvals")

    p_app = sub.add_parser("approve", help="Approve + execute")
    p_app.add_argument("incident_id")
    p_app.add_argument("--approver", default="cli")
    p_app.add_argument("--note", default="")
    add_dryrun(p_app)

    p_den = sub.add_parser("deny", help="Deny a pending approval")
    p_den.add_argument("incident_id")
    p_den.add_argument("--approver", default="cli")
    p_den.add_argument("--note", default="")

    sub.add_parser("status", help="Show incident + pending summary")

    p_hist = sub.add_parser("history", help="Show one incident's history")
    p_hist.add_argument("incident_id")

    p_watch = sub.add_parser("watch", help="Loop cycle forever")
    p_watch.add_argument("--interval", type=int, default=30)
    add_dryrun(p_watch)

    args = parser.parse_args()

    if args.command == "tick":
        sys.exit(cmd_tick(args))
    if args.command == "run":
        sys.exit(cmd_run(args))
    if args.command == "cycle":
        sys.exit(cmd_cycle(args))
    if args.command == "approvals":
        sys.exit(cmd_approvals(args))
    if args.command == "approve":
        sys.exit(cmd_approve(args))
    if args.command == "deny":
        sys.exit(cmd_deny(args))
    if args.command == "status":
        sys.exit(cmd_status(args))
    if args.command == "history":
        sys.exit(cmd_history(args))
    if args.command == "watch":
        sys.exit(cmd_watch(args))


if __name__ == "__main__":
    main()
