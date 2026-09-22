#!/usr/bin/env python3
"""
Netwroxia — Phase C3: Incident Lifecycle Engine

Reads B8's rca/latest_rca.json, creates or updates incidents in C2's
SQLite store, drives state transitions based on severity, logs every
change to C1's audit log. Idempotent across repeated runs.

Reconciles: incidents present in the store but absent from the current
RCA input are auto-closed (RESOLVED -> CLOSED).

Does NOT execute actions. Stops at AWAITING_APPROVAL (CRITICAL) or
INVESTIGATING (LOW/MEDIUM/HIGH). The executor (C14) takes over from there.

CLI:
    python3 incidents/incident_engine.py tick      # one reconciliation pass
    python3 incidents/incident_engine.py tick --dry-run
    python3 incidents/incident_engine.py status    # show state counts + active
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from incidents import incident_store as store
from audit import audit_logger as audit

# ── CONFIG ──────────────────────────────────────────────────────────────────
RCA_PATH = PROJECT_ROOT / "rca" / "latest_rca.json"
BIZ_IMPACT_PATH = PROJECT_ROOT / "impact" / "latest_business_impact.json"

# States the engine refuses to auto-advance out of
ENGINE_PAUSED_STATES = {
    "INVESTIGATING",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "VERIFIED",
    "FAILED",
    "RESOLVED",
    "ROLLED_BACK",
    "ESCALATED",
    "CLOSED",
}

# Severity -> auto-advance chain
AUTO_ADVANCE = {
    "CRITICAL": ["INVESTIGATING", "AWAITING_APPROVAL"],
    "HIGH":     ["INVESTIGATING"],
    "MEDIUM":   ["INVESTIGATING"],
    "LOW":      ["INVESTIGATING"],
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[WARN] malformed JSON in {path}: {e}")
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── RCA PARSING ─────────────────────────────────────────────────────────────
def extract_rca_incidents(rca: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert B8's incidents array into the flat shape the engine needs:
      {incident_id, severity, root_router, title, snapshot}
    """
    out: List[Dict[str, Any]] = []
    for inc in rca.get("incidents", []):
        iid = inc.get("incident_id")
        if not iid:
            continue
        summary = inc.get("summary", {})
        severity = summary.get("severity", "UNKNOWN")
        root = summary.get("root", "")
        # Title: short human label
        chain = summary.get("chain", [])
        title = (
            f"{severity} @ {root}"
            + (f" ({len(chain)-1} downstream)" if len(chain) > 1 else "")
        )
        out.append({
            "incident_id": iid,
            "severity": severity,
            "root_router": root,
            "title": title,
            "snapshot": inc,
        })
    return out


# ── TRANSITIONS ─────────────────────────────────────────────────────────────
def _advance_detected(
    incident_id: str,
    severity: str,
    dry_run: bool,
) -> List[str]:
    """
    Advance a DETECTED incident through its severity's auto-chain.
    Returns the list of states reached (empty if already past).
    """
    chain = AUTO_ADVANCE.get(severity, [])
    reached: List[str] = []

    for target in chain:
        if dry_run:
            print(f"  [DRY] {incident_id}: DETECTED -> {target}")
            reached.append(target)
            continue
        try:
            store.update_state(
                incident_id=incident_id,
                new_state=target,
                reason=f"auto-advance for severity {severity}",
                actor="auto",
            )
            audit.log(
                event="incident.state_changed",
                incident_id=incident_id,
                actor="auto",
                data={"to_state": target, "reason": "auto_advance",
                      "severity": severity},
            )
            reached.append(target)
        except ValueError as e:
            audit.log(
                event="incident.transition_rejected",
                incident_id=incident_id,
                actor="auto",
                data={"to_state": target, "error": str(e)},
            )
            print(f"  [WARN] {incident_id}: -> {target} rejected: {e}")
            break

    return reached


def _close_orphan(incident_id: str, dry_run: bool) -> bool:
    """Transition an incident to RESOLVED then CLOSED."""
    if dry_run:
        print(f"  [DRY] {incident_id}: -> RESOLVED -> CLOSED (orphan)")
        return True
    try:
        store.update_state(
            incident_id=incident_id,
            new_state="RESOLVED",
            reason="RCA no longer present; auto-recovered",
            actor="auto",
        )
        audit.log(
            event="incident.state_changed",
            incident_id=incident_id,
            actor="auto",
            data={"to_state": "RESOLVED", "reason": "orphan_closed"},
        )
    except ValueError as e:
        audit.log(
            event="incident.transition_rejected",
            incident_id=incident_id,
            actor="auto",
            data={"to_state": "RESOLVED", "error": str(e)},
        )
        return False

    try:
        store.update_state(
            incident_id=incident_id,
            new_state="CLOSED",
            reason="auto-closed after recovery",
            actor="auto",
        )
        audit.log(
            event="incident.state_changed",
            incident_id=incident_id,
            actor="auto",
            data={"to_state": "CLOSED", "reason": "orphan_closed"},
        )
        return True
    except ValueError as e:
        audit.log(
            event="incident.transition_rejected",
            incident_id=incident_id,
            actor="auto",
            data={"to_state": "CLOSED", "error": str(e)},
        )
        return False


# ── MAIN TICK ───────────────────────────────────────────────────────────────
def tick(dry_run: bool = False) -> Dict[str, Any]:
    """
    One reconciliation pass:
      - Upsert every incident present in the RCA input
      - Auto-advance each newly-detected incident
      - Close orphans (in store, not in RCA)

    Returns a summary dict.
    """
    rca = _load_json(RCA_PATH)
    if rca is None:
        print(f"[WARN] No RCA found at {RCA_PATH}")
        rca = {"incidents": []}

    rca_incidents = extract_rca_incidents(rca)
    active_ids = {i["incident_id"] for i in rca_incidents}

    summary = {
        "ts": _now_iso(),
        "rca_incidents": len(rca_incidents),
        "created": 0,
        "observed": 0,
        "advanced": 0,
        "orphans_closed": 0,
        "skipped_terminal": 0,
        "dry_run": dry_run,
    }

    print(f"[TICK] {len(rca_incidents)} RCA incident(s) to process")

    # Phase 1: upsert + advance
    for rca_inc in rca_incidents:
        iid = rca_inc["incident_id"]

        existing = store.get_incident(iid) if not dry_run else None
        if existing is None:
            if dry_run:
                print(f"  [DRY] create {iid}  sev={rca_inc['severity']}  "
                      f"root={rca_inc['root_router']}")
                summary["created"] += 1
                summary["advanced"] += len(AUTO_ADVANCE.get(rca_inc["severity"], []))
                continue

            store.upsert_incident(
                incident_id=iid,
                severity=rca_inc["severity"],
                root_router=rca_inc["root_router"],
                title=rca_inc["title"],
                rca_snapshot=rca_inc["snapshot"],
            )
            audit.log(
                event="incident.created",
                incident_id=iid,
                router=rca_inc["root_router"],
                actor="auto",
                data={"severity": rca_inc["severity"],
                      "title": rca_inc["title"]},
            )
            print(f"  [+] created {iid}  sev={rca_inc['severity']}")
            summary["created"] += 1

            reached = _advance_detected(iid, rca_inc["severity"], dry_run=False)
            summary["advanced"] += len(reached)
            continue

        # Existing incident
        current_state = existing.get("state")
        if current_state in {"CLOSED"}:
            summary["skipped_terminal"] += 1
            print(f"  [=] {iid}: skipped (terminal {current_state})")
            continue

        # Refresh severity/root/title (they may have changed)
        if not dry_run:
            store.upsert_incident(
                incident_id=iid,
                severity=rca_inc["severity"],
                root_router=rca_inc["root_router"],
                title=rca_inc["title"],
                rca_snapshot=None,  # preserve original snapshot
            )
            audit.log(
                event="incident.observed",
                incident_id=iid,
                router=rca_inc["root_router"],
                actor="auto",
                data={"state": current_state, "severity": rca_inc["severity"]},
            )
        print(f"  [=] {iid}: observed (state={current_state})")
        summary["observed"] += 1

        # Auto-advance if still DETECTED
        if current_state == "DETECTED":
            reached = _advance_detected(iid, rca_inc["severity"], dry_run=dry_run)
            summary["advanced"] += len(reached)

    # Phase 2: reconcile orphans
    all_stored = store.list_incidents(limit=1000)
    for inc in all_stored:
        iid = inc["incident_id"]
        state = inc["state"]
        if iid in active_ids:
            continue
        if state in {"CLOSED", "ESCALATED"}:
            continue
        # Orphan — RCA no longer mentions it
        print(f"  [-] {iid}: orphan (state={state})")
        if _close_orphan(iid, dry_run=dry_run):
            summary["orphans_closed"] += 1

    return summary


# ── STATUS ──────────────────────────────────────────────────────────────────
def status() -> None:
    counts = store.count_by_state()
    total = sum(counts.values())
    print("=" * 72)
    print(" NETWROXIA INCIDENT STATUS")
    print("=" * 72)
    print(f" Total incidents : {total}")
    for s, n in sorted(counts.items()):
        if n > 0:
            print(f"   {s:20s}  {n}")
    print("-" * 72)

    active = store.list_incidents(limit=20)
    if not active:
        print(" (no incidents)")
    else:
        print(" Recent (max 20):")
        for inc in active:
            print(f"   {inc['incident_id'][:40]:40s}  "
                  f"{inc['state']:18s}  "
                  f"sev={inc['severity']:8s}  "
                  f"root={inc['root_router'] or '-':18s}  "
                  f"updated={inc['updated_at']}")
    print("=" * 72)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Incident Lifecycle Engine (C3)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_tick = sub.add_parser("tick", help="One reconciliation pass")
    p_tick.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="Show current state counts")

    args = parser.parse_args()

    if args.command == "tick":
        summary = tick(dry_run=args.dry_run)
        print()
        print("[SUMMARY]")
        for k, v in summary.items():
            print(f"   {k:20s}  {v}")
        print()
    elif args.command == "status":
        status()


if __name__ == "__main__":
    main()
