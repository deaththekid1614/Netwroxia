#!/usr/bin/env python3
"""
Netwroxia — Phase C6: Approval Gate Guardrail

Human-in-the-loop gate for CRITICAL severity incidents. Stores
pending/approved/denied decisions in a single JSON file. Atomic writes,
idempotent requests, no flapping.

Storage: remediation/engine/pending_approval.json
Override: NETWROXIA_PENDING_PATH env var (for tests)

CLI:
    python3 remediation/guardrails/approval_gate.py request \\
        --incident-id INC-X --severity CRITICAL \\
        --root-router ZO-Bengaluru --action reroute_traffic --reason "..."
    python3 remediation/guardrails/approval_gate.py check --incident-id INC-X
    python3 remediation/guardrails/approval_gate.py approve --incident-id INC-X \\
        --approver death-kid --note "checked"
    python3 remediation/guardrails/approval_gate.py deny --incident-id INC-X \\
        --approver death-kid --note "not now"
    python3 remediation/guardrails/approval_gate.py list
    python3 remediation/guardrails/approval_gate.py clear --incident-id INC-X
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit import audit_logger as audit

# ── CONFIG ──────────────────────────────────────────────────────────────────
PENDING_PATH = Path(os.environ.get(
    "NETWROXIA_PENDING_PATH",
    str(PROJECT_ROOT / "remediation" / "engine" / "pending_approval.json"),
))

SCHEMA_VERSION = 1
VALID_DECISIONS = {"PENDING", "APPROVED", "DENIED"}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_store() -> Dict[str, Any]:
    if not PENDING_PATH.exists():
        return {"version": SCHEMA_VERSION, "pending": {}}
    try:
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": SCHEMA_VERSION, "pending": {}}
    if not isinstance(data, dict) or "pending" not in data:
        return {"version": SCHEMA_VERSION, "pending": {}}
    return data


def _save_store(store: Dict[str, Any]) -> None:
    """Atomic write: temp file in same dir + os.replace."""
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".pending_", suffix=".json",
        dir=str(PENDING_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, PENDING_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def requires_approval(severity: str) -> bool:
    """CRITICAL severity always requires human approval."""
    return (severity or "").upper() == "CRITICAL"


def request_approval(
    incident_id: str,
    severity: str,
    root_router: str,
    action: str,
    reason: str = "",
    actor: str = "auto",
) -> Dict[str, Any]:
    """
    Request approval for an incident. Idempotent: if the entry already
    exists and is PENDING, only the reason gets updated (requested_at
    preserved). If already APPROVED/DENIED, the decision is preserved
    and the request is a no-op.
    """
    if not incident_id:
        raise ValueError("incident_id is required")

    store = _load_store()
    pending = store.setdefault("pending", {})
    now = _now_iso()

    existing = pending.get(incident_id)
    if existing is not None:
        if existing.get("decision") in {"APPROVED", "DENIED"}:
            # Preserve decision
            return existing
        # PENDING -> update reason only
        existing["reason"] = reason or existing.get("reason", "")
        existing["severity"] = severity or existing.get("severity", "UNKNOWN")
        existing["root_router"] = root_router or existing.get("root_router", "")
        existing["action"] = action or existing.get("action", "")
        _save_store(store)
        audit.log(
            event="approval.requested",
            incident_id=incident_id,
            router=root_router,
            actor=actor,
            data={"action": action, "severity": severity, "refresh": True},
        )
        return existing

    entry = {
        "incident_id": incident_id,
        "severity": severity,
        "root_router": root_router,
        "action": action,
        "reason": reason,
        "requested_at": now,
        "requested_by": actor,
        "decision": "PENDING",
        "decided_at": None,
        "decided_by": None,
        "note": "",
    }
    pending[incident_id] = entry
    _save_store(store)

    audit.log(
        event="approval.requested",
        incident_id=incident_id,
        router=root_router,
        actor=actor,
        data={"action": action, "severity": severity},
    )
    return entry


def check_approval(incident_id: str) -> Dict[str, Any]:
    """
    Return the current approval state for an incident.
    Always returns a dict with at least {"status": <one of ...>}.
    """
    if not incident_id:
        return {"status": "NOT_FOUND", "incident_id": ""}
    store = _load_store()
    entry = store.get("pending", {}).get(incident_id)
    if entry is None:
        return {"status": "NOT_FOUND", "incident_id": incident_id}
    out = dict(entry)
    out["status"] = entry.get("decision", "PENDING")
    return out


def is_approved(incident_id: str) -> bool:
    return check_approval(incident_id).get("status") == "APPROVED"


def approve(
    incident_id: str,
    approver: str = "human:unknown",
    note: str = "",
) -> Dict[str, Any]:
    """Mark an incident APPROVED. Refuses if not PENDING."""
    store = _load_store()
    entry = store.get("pending", {}).get(incident_id)
    if entry is None:
        raise ValueError(f"no approval request for incident: {incident_id}")
    if entry.get("decision") != "PENDING":
        raise ValueError(
            f"cannot approve: decision is already {entry.get('decision')}"
        )

    now = _now_iso()
    entry["decision"] = "APPROVED"
    entry["decided_at"] = now
    entry["decided_by"] = approver
    entry["note"] = note
    _save_store(store)

    audit.log(
        event="approval.approved",
        incident_id=incident_id,
        router=entry.get("root_router"),
        actor=approver,
        data={"action": entry.get("action"), "note": note},
    )
    return entry


def deny(
    incident_id: str,
    approver: str = "human:unknown",
    note: str = "",
) -> Dict[str, Any]:
    """Mark an incident DENIED. Refuses if not PENDING."""
    store = _load_store()
    entry = store.get("pending", {}).get(incident_id)
    if entry is None:
        raise ValueError(f"no approval request for incident: {incident_id}")
    if entry.get("decision") != "PENDING":
        raise ValueError(
            f"cannot deny: decision is already {entry.get('decision')}"
        )

    now = _now_iso()
    entry["decision"] = "DENIED"
    entry["decided_at"] = now
    entry["decided_by"] = approver
    entry["note"] = note
    _save_store(store)

    audit.log(
        event="approval.denied",
        incident_id=incident_id,
        router=entry.get("root_router"),
        actor=approver,
        data={"action": entry.get("action"), "note": note},
    )
    return entry


def clear(incident_id: str) -> bool:
    """Remove the entry for an incident. Returns True if removed."""
    store = _load_store()
    pending = store.get("pending", {})
    if incident_id not in pending:
        return False
    del pending[incident_id]
    _save_store(store)
    audit.log(
        event="approval.cleared",
        incident_id=incident_id,
        actor="auto",
        data={},
    )
    return True


def list_pending() -> List[Dict[str, Any]]:
    """Return every entry currently in the store (any decision)."""
    store = _load_store()
    return list(store.get("pending", {}).values())


def list_by_decision(decision: str) -> List[Dict[str, Any]]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"unknown decision: {decision}")
    return [e for e in list_pending() if e.get("decision") == decision]


# ── CLI ─────────────────────────────────────────────────────────────────────
def _print_entry(e: Dict[str, Any]) -> None:
    status = e.get("decision", "?")
    print(f"  {e.get('incident_id', '?')[:40]:40s}  "
          f"[{status:9s}]  "
          f"sev={e.get('severity', '?'):8s}  "
          f"action={e.get('action', '?'):18s}  "
          f"root={e.get('root_router') or '-':18s}")


def cmd_request(args) -> int:
    entry = request_approval(
        incident_id=args.incident_id,
        severity=args.severity,
        root_router=args.root_router,
        action=args.action,
        reason=args.reason,
        actor="cli",
    )
    print(json.dumps(entry, indent=2))
    return 0


def cmd_check(args) -> int:
    result = check_approval(args.incident_id)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "APPROVED" else 1


def cmd_approve(args) -> int:
    try:
        entry = approve(args.incident_id, args.approver, args.note)
    except ValueError as e:
        print(f"[DENY] {e}")
        return 1
    print(json.dumps(entry, indent=2))
    return 0


def cmd_deny(args) -> int:
    try:
        entry = deny(args.incident_id, args.approver, args.note)
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1
    print(json.dumps(entry, indent=2))
    return 0


def cmd_list(_args) -> int:
    entries = list_pending()
    if not entries:
        print("(no approval entries)")
        return 0
    print(f"[LIST] {len(entries)} entry(ies)")
    for e in entries:
        _print_entry(e)
    return 0


def cmd_clear(args) -> int:
    ok = clear(args.incident_id)
    if ok:
        print(f"[OK] cleared {args.incident_id}")
        return 0
    print(f"[MISS] no entry for {args.incident_id}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Approval Gate (C6)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_req = sub.add_parser("request")
    p_req.add_argument("--incident-id", required=True)
    p_req.add_argument("--severity", required=True)
    p_req.add_argument("--root-router", required=True)
    p_req.add_argument("--action", required=True)
    p_req.add_argument("--reason", default="")

    p_chk = sub.add_parser("check")
    p_chk.add_argument("--incident-id", required=True)

    p_app = sub.add_parser("approve")
    p_app.add_argument("--incident-id", required=True)
    p_app.add_argument("--approver", required=True)
    p_app.add_argument("--note", default="")

    p_den = sub.add_parser("deny")
    p_den.add_argument("--incident-id", required=True)
    p_den.add_argument("--approver", required=True)
    p_den.add_argument("--note", default="")

    sub.add_parser("list")

    p_clr = sub.add_parser("clear")
    p_clr.add_argument("--incident-id", required=True)

    args = parser.parse_args()
    if args.command == "request":
        sys.exit(cmd_request(args))
    if args.command == "check":
        sys.exit(cmd_check(args))
    if args.command == "approve":
        sys.exit(cmd_approve(args))
    if args.command == "deny":
        sys.exit(cmd_deny(args))
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "clear":
        sys.exit(cmd_clear(args))


if __name__ == "__main__":
    main()
