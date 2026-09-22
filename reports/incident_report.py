#!/usr/bin/env python3
"""
Netwroxia — Phase D9: Incident Report

Deep-dive report for a single incident. Aggregates:
  - incident record + transitions       (C2 store)
  - audit trail filtered by incident_id (C1)
  - snapshots                            (C7)
  - escalations                          (C13)
  - actions taken                        (subset of audit)

Output: reports/incidents/<incident_id>.json

CLI:
    python3 reports/incident_report.py list
    python3 reports/incident_report.py generate <incident_id>
    python3 reports/incident_report.py generate <incident_id> --show
    python3 reports/incident_report.py latest [--show]
"""

import argparse
import json
import os
import re
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
from remediation.guardrails import rollback

# ── CONFIG ──────────────────────────────────────────────────────────────────
OUTPUT_DIR = PROJECT_ROOT / "reports" / "incidents"

ESCALATIONS_DIR = Path(os.environ.get(
    "NETWROXIA_ESCALATIONS_DIR",
    str(PROJECT_ROOT / "remediation" / "engine" / "escalations"),
))

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _sanitize(s: str) -> str:
    return _SAFE_RE.sub("_", s or "unknown")


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _duration_seconds(created: Optional[datetime],
                      resolved: Optional[datetime]) -> Optional[float]:
    if created is None:
        return None
    end = resolved if resolved is not None else datetime.now(timezone.utc)
    delta = (end - created).total_seconds()
    return round(delta, 2) if delta >= 0 else None


# ── COLLECTORS ──────────────────────────────────────────────────────────────
def _collect_audit_events(incident_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
    """Filter audit log for events tied to this incident."""
    all_events = audit.tail(n=limit)
    return [e for e in all_events if e.get("incident_id") == incident_id]


def _collect_snapshots(incident_id: str) -> List[Dict[str, Any]]:
    """Return all snapshots for this incident (newest first)."""
    try:
        return rollback.list_snapshots(incident_id=incident_id, limit=200)
    except Exception:
        return []


def _collect_escalations(incident_id: str) -> List[Dict[str, Any]]:
    if not ESCALATIONS_DIR.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(ESCALATIONS_DIR.glob("*.json")):
        data = _load_json(path)
        if data is None:
            continue
        if data.get("incident_id") == incident_id:
            out.append(data)
    return out


def _collect_actions(audit_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Subset: action.executed / action.failed / action.rolled_back."""
    keys = {"action.executed", "action.failed", "action.rolled_back"}
    return [e for e in audit_events if e.get("event") in keys]


# ── TIMELINE ────────────────────────────────────────────────────────────────
def _build_timeline(
    incident: Dict[str, Any],
    transitions: List[Dict[str, Any]],
    audit_events: List[Dict[str, Any]],
    snapshots: List[Dict[str, Any]],
    escalations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge transitions, audit events, snapshots, escalations into one
    chronologically-sorted list of timeline entries.
    """
    items: List[Dict[str, Any]] = []

    for t in transitions:
        items.append({
            "ts": t.get("ts"),
            "kind": "transition",
            "text": f"{t.get('from_state') or '-'} -> {t['to_state']}  "
                    f"({t.get('reason') or 'no reason'})",
            "actor": t.get("actor"),
            "raw": t,
        })

    for e in audit_events:
        items.append({
            "ts": e.get("ts"),
            "kind": "audit",
            "text": e.get("event"),
            "actor": e.get("actor"),
            "raw": e,
        })

    for s in snapshots:
        items.append({
            "ts": s.get("created_at"),
            "kind": "snapshot",
            "text": f"snapshot {s.get('snapshot_id')}  "
                    f"(action={s.get('action')})",
            "actor": "auto",
            "raw": s,
        })

    for esc in escalations:
        items.append({
            "ts": esc.get("created_at"),
            "kind": "escalation",
            "text": f"escalation {esc.get('escalation_id')}  "
                    f"(severity={esc.get('severity')})",
            "actor": esc.get("created_by", "auto"),
            "raw": esc,
        })

    items.sort(key=lambda x: x.get("ts") or "")
    return items


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_incident_report(incident_id: str) -> Optional[Dict[str, Any]]:
    incident = store.get_incident(incident_id)
    if incident is None:
        return None

    transitions = store.get_transitions(incident_id)
    audit_events = _collect_audit_events(incident_id)
    snapshots = _collect_snapshots(incident_id)
    escalations = _collect_escalations(incident_id)
    actions = _collect_actions(audit_events)

    created = _parse_iso(incident.get("created_at"))
    resolved = _parse_iso(incident.get("resolved_at"))
    duration_sec = _duration_seconds(created, resolved)

    rca_snapshot = None
    raw = incident.get("rca_snapshot")
    if raw:
        try:
            rca_snapshot = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            rca_snapshot = None

    timeline = _build_timeline(incident, transitions, audit_events,
                               snapshots, escalations)

    return {
        "report_type": "incident",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "summary": {
            "state": incident.get("state"),
            "severity": incident.get("severity"),
            "root_router": incident.get("root_router"),
            "title": incident.get("title"),
            "created_at": incident.get("created_at"),
            "updated_at": incident.get("updated_at"),
            "resolved_at": incident.get("resolved_at"),
            "duration_seconds": duration_sec,
        },
        "rca_snapshot": rca_snapshot,
        "transitions": transitions,
        "audit_trail": audit_events,
        "actions_taken": actions,
        "snapshots": snapshots,
        "escalations": escalations,
        "timeline": timeline,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_report(report: Dict[str, Any], show_events: bool = False) -> None:
    hr = "─" * 78
    s = report["summary"]
    print(hr)
    print(f" INCIDENT REPORT  {report['incident_id']}")
    print(f" generated_at = {report['generated_at']}")
    print(hr)

    print(f" State         : {s['state']}")
    print(f" Severity      : {s['severity']}")
    print(f" Root router   : {s['root_router']}")
    print(f" Title         : {s['title']}")
    print(f" Created at    : {s['created_at']}")
    print(f" Resolved at   : {s['resolved_at']}")
    dur = s.get("duration_seconds")
    if dur is not None:
        print(f" Duration      : {dur:.1f}s  ({dur/60.0:.2f} min)")
    print(hr)

    if report["rca_snapshot"]:
        snap = report["rca_snapshot"]
        root = snap.get("summary", {}).get("root")
        chain = snap.get("summary", {}).get("chain", [])
        print(" RCA SNAPSHOT")
        print(f"   root={root}  chain={' -> '.join(chain) if chain else '-'}")
        rc = snap.get("root_cause", {})
        if rc:
            print(f"   confidence={rc.get('combined_confidence')}  "
                  f"graph={rc.get('graph_confidence')}  "
                  f"hist={rc.get('historical_match_score')}")
        if snap.get("narrative"):
            print(f"   narrative: {snap['narrative'][:200]}")
        print(hr)

    print(f" COUNTS: transitions={len(report['transitions'])}  "
          f"audit={len(report['audit_trail'])}  "
          f"actions={len(report['actions_taken'])}  "
          f"snapshots={len(report['snapshots'])}  "
          f"escalations={len(report['escalations'])}")
    print(hr)

    print(" TIMELINE")
    for item in report["timeline"]:
        tag = item["kind"][:4].upper()
        print(f"   {item.get('ts')}  [{tag:4s}]  "
              f"{item.get('text', '')[:80]:80s}  "
              f"by {item.get('actor') or '-'}")

    if show_events and report["actions_taken"]:
        print(hr)
        print(" ACTION DETAILS")
        for a in report["actions_taken"]:
            print(f"   {a.get('ts')}  {a.get('event')}  {a.get('data')}")

    print(hr)


def cmd_list(_args) -> int:
    incidents = store.list_incidents(limit=200)
    if not incidents:
        print("(no incidents)")
        return 0
    print(f"[LIST] {len(incidents)} incident(s)")
    for i in incidents:
        print(f"  {i['incident_id'][:40]:40s}  "
              f"{i['state']:18s}  "
              f"sev={i['severity']:8s}  "
              f"root={i['root_router'] or '-':18s}  "
              f"updated={i['updated_at']}")
    return 0


def cmd_generate(args) -> int:
    report = build_incident_report(args.incident_id)
    if report is None:
        print(f"[MISS] incident not found: {args.incident_id}")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{_sanitize(args.incident_id)}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    if args.show:
        print_report(report, show_events=args.verbose)
    else:
        print(f"[OK] incident report written to {_safe_relpath(path)}")
    return 0


def cmd_latest(args) -> int:
    incidents = store.list_incidents(limit=1)
    if not incidents:
        print("(no incidents)")
        return 0
    iid = incidents[0]["incident_id"]
    report = build_incident_report(iid)
    if report is None:
        print(f"[MISS] {iid}")
        return 1

    # Always persist (consistent with cmd_generate which writes even --show)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{_sanitize(iid)}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    if args.show:
        print_report(report, show_events=args.verbose)
    else:
        print(f"[OK] incident report written to {_safe_relpath(path)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Incident Report (D9)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List incidents")

    p_gen = sub.add_parser("generate", help="Build report for one incident")
    p_gen.add_argument("incident_id")
    p_gen.add_argument("--show", action="store_true")
    p_gen.add_argument("--verbose", action="store_true",
                       help="Include action details in --show output")

    p_lat = sub.add_parser("latest", help="Report on the most recent incident")
    p_lat.add_argument("--show", action="store_true")
    p_lat.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "generate":
        sys.exit(cmd_generate(args))
    if args.command == "latest":
        sys.exit(cmd_latest(args))


if __name__ == "__main__":
    main()
