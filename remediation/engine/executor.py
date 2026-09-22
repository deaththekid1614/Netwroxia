#!/usr/bin/env python3
"""
Netwroxia — Phase C14: Executor

Full lifecycle orchestrator for one incident:

    load incident
      -> decide action            (C4)
      -> rate limit check         (C5)
      -> approval gate            (C6)
      -> action preconditions     (C8..C13)
      -> snapshot                 (C7)
      -> execute                  (C8..C13)
      -> verify                   (C8..C13)
      -> resolve OR rollback      (C7, C8..C13)
      -> audit each step          (C1)
      -> incident state update    (C2)

CLI:
    python3 remediation/engine/executor.py run --incident-id <ID> [--dry-run]
    python3 remediation/engine/executor.py run-all [--dry-run]
    python3 remediation/engine/executor.py dryrun --incident-id <ID>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit import audit_logger as audit
from incidents import incident_store as store
from remediation.actions import _base as actions_base  # registers all actions
from remediation.engine import decision_tree as dt
from remediation.guardrails import approval_gate, rate_limiter, rollback

# ── CONFIG ──────────────────────────────────────────────────────────────────
PREDICTION_PATH = PROJECT_ROOT / "ml" / "inference" / "latest_prediction.json"


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _extract_root_role(incident: Dict[str, Any]) -> str:
    """Pull root_role from the incident's RCA snapshot, fallback to store."""
    raw = incident.get("rca_snapshot")
    if raw:
        try:
            snap = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            snap = {}
        summary = snap.get("summary", {}) if isinstance(snap, dict) else {}
        role = summary.get("root_role")
        if role:
            return role
    # Fallback: read from store-level info
    return "unknown"


def _extract_fault_hint(root_router: str) -> Optional[str]:
    """
    Look up the root router's top_feature in the current prediction and
    map it to a decision_tree fault hint. Returns None if unavailable.
    """
    if not root_router or not PREDICTION_PATH.exists():
        return None
    try:
        with open(PREDICTION_PATH) as f:
            pred = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    for p in pred.get("predictions", []):
        if p.get("router") != root_router:
            continue
        top = (p.get("top_feature") or "").lower()
        if "packet_loss" in top or "loss" in top:
            return "packet_loss"
        if "response_ms" in top or "latency" in top:
            return "latency"
        if "state_ok" in top or "bgp" in top:
            return "bgp"
        if "ospf" in top or top == "count":
            return "ospf"
    return None


def _safe_transition(
    incident_id: str,
    new_state: str,
    reason: str = "",
    actor: str = "auto",
) -> bool:
    """Transition incident state; log rejection but don't raise."""
    try:
        store.update_state(incident_id, new_state, reason=reason, actor=actor)
        audit.log(
            event="incident.state_changed",
            incident_id=incident_id,
            actor=actor,
            data={"to_state": new_state, "reason": reason},
        )
        return True
    except ValueError as e:
        audit.log(
            event="incident.transition_rejected",
            incident_id=incident_id,
            actor=actor,
            data={"to_state": new_state, "error": str(e)},
        )
        return False


# ── MAIN EXECUTOR ───────────────────────────────────────────────────────────
def execute_incident(
    incident_id: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Drive the full lifecycle for one incident.

    Returns a structured result dict. Never raises on normal failures.
    """
    # ── 1. Load incident ───────────────────────────────────────────────
    incident = store.get_incident(incident_id)
    if incident is None:
        return {
            "ok": False,
            "stage": "load_incident",
            "error": f"incident not found: {incident_id}",
        }

    severity = incident.get("severity", "UNKNOWN")
    root_router = incident.get("root_router", "") or ""
    root_role = _extract_root_role(incident)

    audit.log(
        event="execution.started",
        incident_id=incident_id,
        router=root_router,
        actor="auto",
        data={"severity": severity, "dry_run": dry_run,
              "state": incident.get("state")},
    )

    # Skip terminal states
    if incident.get("state") in {"CLOSED", "ESCALATED", "ROLLED_BACK"}:
        return {
            "ok": False,
            "stage": "skip_terminal",
            "error": f"incident is terminal: {incident.get('state')}",
        }

    # ── 2. Decide action ───────────────────────────────────────────────
    fault_hint = _extract_fault_hint(root_router)
    decision = dt.decide(
        severity=severity,
        root_role=root_role,
        root_router=root_router,
        fault_hint=fault_hint,
    )
    action_name = decision["action"]
    requires_approval = bool(decision["requires_approval"])

    audit.log(
        event="execution.decision",
        incident_id=incident_id,
        router=root_router,
        actor="auto",
        data={"action": action_name, "source": decision["action_source"],
              "confidence": decision["confidence"],
              "requires_approval": requires_approval,
              "reason": decision["reason"],
              "fault_hint": fault_hint},
    )

    # ── 3. Rate limit ──────────────────────────────────────────────────
    # Only check for real actions (skip no_op / escalate)
    if action_name not in {"no_op", "escalate_to_human"}:
        rl = rate_limiter.check(root_router)
        if not rl["allowed"]:
            audit.log(
                event="execution.throttled",
                incident_id=incident_id,
                router=root_router,
                actor="auto",
                data={"action": action_name, "reason": rl["reason"],
                      "wait_seconds": rl["wait_seconds"]},
            )
            return {
                "ok": False,
                "stage": "rate_limit",
                "error": rl["reason"],
                "wait_seconds": rl["wait_seconds"],
                "action": action_name,
            }

    # ── 4. Approval gate ───────────────────────────────────────────────
    # Skip approval for actions that do not touch the network.
    if requires_approval and action_name not in {"no_op", "escalate_to_human"}:
        ap = approval_gate.check_approval(incident_id)
        status = ap.get("status")

        if status == "NOT_FOUND":
            approval_gate.request_approval(
                incident_id=incident_id,
                severity=severity,
                root_router=root_router,
                action=action_name,
                reason=decision["reason"],
            )
            _safe_transition(incident_id, "AWAITING_APPROVAL",
                             reason="awaiting human approval")
            audit.log(
                event="execution.awaiting_approval",
                incident_id=incident_id,
                router=root_router,
                actor="auto",
                data={"action": action_name},
            )
            return {
                "ok": False,
                "stage": "awaiting_approval",
                "error": "approval requested; check pending_approval.json",
                "action": action_name,
            }

        if status == "PENDING":
            audit.log(
                event="execution.awaiting_approval",
                incident_id=incident_id,
                router=root_router,
                actor="auto",
                data={"action": action_name, "refresh": True},
            )
            return {
                "ok": False,
                "stage": "awaiting_approval",
                "error": "approval still pending",
                "action": action_name,
            }

        if status == "DENIED":
            _safe_transition(incident_id, "ESCALATED",
                             reason="approval denied by human")
            audit.log(
                event="execution.denied",
                incident_id=incident_id,
                router=root_router,
                actor="human",
                data={"action": action_name, "note": ap.get("note")},
            )
            return {
                "ok": False,
                "stage": "denied",
                "error": "action denied by approver",
                "action": action_name,
            }

    # ── 5. Get action instance ─────────────────────────────────────────
    action = actions_base.get_action(action_name)
    if action is None:
        return {
            "ok": False,
            "stage": "get_action",
            "error": f"action not registered: {action_name}",
        }

    ctx: Dict[str, Any] = {
        "incident_id": incident_id,
        "action": action_name,
        "router": root_router,
        "severity": severity,
        "reason": decision["reason"],
        "dry_run": dry_run,
        "actor": "auto",
    }

    # ── 6. Preconditions ───────────────────────────────────────────────
    ok, reason = action.preconditions(ctx)
    if not ok:
        audit.log(
            event="execution.precondition_failed",
            incident_id=incident_id,
            router=root_router,
            actor="auto",
            data={"action": action_name, "reason": reason},
        )
        return {
            "ok": False,
            "stage": "preconditions",
            "error": reason,
            "action": action_name,
        }

    # ── 7. State -> EXECUTING ──────────────────────────────────────────
    is_real_action = action_name not in {"no_op", "escalate_to_human"}
    if is_real_action:
        _safe_transition(incident_id, "EXECUTING", reason="executor running")

    # ── 8. Snapshot ────────────────────────────────────────────────────
    try:
        snapshot_data = action.snapshot(ctx)
    except Exception as e:
        return {
            "ok": False,
            "stage": "snapshot",
            "error": f"{type(e).__name__}: {e}",
            "action": action_name,
        }

    if not dry_run:
        snap_rec = rollback.save_snapshot(
            incident_id=incident_id,
            action=action_name,
            router=root_router,
            state=snapshot_data,
        )
        ctx["snapshot_id"] = snap_rec["snapshot_id"]
        audit.log(
            event="execution.snapshot_saved",
            incident_id=incident_id,
            router=root_router,
            actor="auto",
            data={"snapshot_id": snap_rec["snapshot_id"],
                  "action": action_name},
        )

    # ── 9. Execute ─────────────────────────────────────────────────────
    try:
        result = action.execute(ctx)
    except Exception as e:
        result = {"executed": False,
                  "note": f"{type(e).__name__}: {e}",
                  "commands": [], "outputs": []}

    # ── 10. State -> VERIFYING ─────────────────────────────────────────
    if is_real_action and not dry_run:
        _safe_transition(incident_id, "VERIFYING", reason="post-execution")

    # ── 11. Verify ─────────────────────────────────────────────────────
    try:
        verified, verify_reason = action.verify(ctx, snapshot_data, result)
    except Exception as e:
        verified, verify_reason = False, f"verify raised {type(e).__name__}: {e}"

    # ── 12a. Success path ──────────────────────────────────────────────
    if verified:
        if is_real_action and not dry_run:
            audit.log(
                event="action.executed",
                incident_id=incident_id,
                router=root_router,
                actor="auto",
                data={
                    "action": action_name,
                    "reason": verify_reason,
                    "commands": result.get("commands", []),
                },
            )
            _safe_transition(incident_id, "RESOLVED",
                             reason=f"verified: {verify_reason}")
        else:
            _safe_transition(incident_id, "RESOLVED",
                             reason=f"verified (dry): {verify_reason}")
        return {
            "ok": True,
            "stage": "verified",
            "action": action_name,
            "reason": verify_reason,
            "executed": result.get("executed", False),
            "commands": result.get("commands", []),
            "dry_run": dry_run,
        }

    # ── 12b. Failure: log + rollback ───────────────────────────────────
    if is_real_action and not dry_run:
        audit.log(
            event="action.failed",
            incident_id=incident_id,
            router=root_router,
            actor="auto",
            data={
                "action": action_name,
                "reason": verify_reason,
                "commands": result.get("commands", []),
            },
        )
        _safe_transition(incident_id, "FAILED",
                         reason=f"verify failed: {verify_reason}")

    # ── 13. Rollback ───────────────────────────────────────────────────
    try:
        rb = action.rollback(ctx, snapshot_data)
    except Exception as e:
        rb = {"rolled_back": False, "note": f"rollback raised {type(e).__name__}: {e}"}

    if rb.get("rolled_back"):
        if not dry_run and ctx.get("snapshot_id"):
            rollback.mark_restored(
                ctx["snapshot_id"],
                actor="auto",
                note=f"verify failed: {verify_reason}",
            )
        if is_real_action and not dry_run:
            audit.log(
                event="action.rolled_back",
                incident_id=incident_id,
                router=root_router,
                actor="auto",
                data={"action": action_name, "note": rb.get("note", "")},
            )
            _safe_transition(incident_id, "ROLLED_BACK",
                             reason="verify failed, rolled back")
        return {
            "ok": False,
            "stage": "rolled_back",
            "action": action_name,
            "reason": verify_reason,
            "rollback_note": rb.get("note", ""),
        }

    # Rollback could not recover -> escalate
    if is_real_action and not dry_run:
        _safe_transition(incident_id, "ESCALATED",
                         reason=f"rollback failed: {rb.get('note', '')}")
    audit.log(
        event="execution.escalated",
        incident_id=incident_id,
        router=root_router,
        actor="auto",
        data={"action": action_name, "verify_reason": verify_reason,
              "rollback_note": rb.get("note", "")},
    )
    return {
        "ok": False,
        "stage": "escalated",
        "action": action_name,
        "reason": verify_reason,
        "rollback_note": rb.get("note", ""),
    }


# ── RUN ALL ─────────────────────────────────────────────────────────────────
def run_all(dry_run: bool = False) -> Dict[str, Any]:
    """Execute every non-terminal incident that's ready for action."""
    all_incidents = store.list_incidents(limit=100)
    results = []
    for inc in all_incidents:
        if inc.get("state") in {"CLOSED", "ROLLED_BACK", "ESCALATED"}:
            continue
        # Only act on incidents that have been advanced by C3
        if inc.get("state") not in {"INVESTIGATING", "AWAITING_APPROVAL"}:
            continue
        results.append({
            "incident_id": inc["incident_id"],
            "result": execute_incident(inc["incident_id"], dry_run=dry_run),
        })
    return {
        "total": len(results),
        "results": results,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_run(args) -> int:
    result = execute_incident(args.incident_id, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_dryrun(args) -> int:
    result = execute_incident(args.incident_id, dry_run=True)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_run_all(args) -> int:
    summary = run_all(dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Executor (C14)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Execute one incident")
    p_run.add_argument("--incident-id", required=True)
    p_run.add_argument("--dry-run", action="store_true")

    p_dry = sub.add_parser("dryrun", help="Same as run --dry-run")
    p_dry.add_argument("--incident-id", required=True)

    p_all = sub.add_parser("run-all", help="Execute every ready incident")
    p_all.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args))
    if args.command == "dryrun":
        sys.exit(cmd_dryrun(args))
    if args.command == "run-all":
        sys.exit(cmd_run_all(args))


if __name__ == "__main__":
    main()
