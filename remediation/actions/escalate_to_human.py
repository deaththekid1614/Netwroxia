#!/usr/bin/env python3
"""
Netwroxia — Phase C13: Escalate to Human

The final fallback action. Writes a structured escalation file for
human review. Never touches the network.

Used when:
  - severity is CRITICAL and root is HO (safety override)
  - root role is unknown
  - decision tree explicitly chooses to escalate
  - guardrails block an automated action

Registered as: "escalate_to_human"
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from remediation.actions._base import (  # noqa: E402
    Action,
    register_action,
)

# ── CONFIG ──────────────────────────────────────────────────────────────────
ESCALATIONS_DIR = Path(os.environ.get(
    "NETWROXIA_ESCALATIONS_DIR",
    str(PROJECT_ROOT / "remediation" / "engine" / "escalations"),
))


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s or "unknown")


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".esc_", suffix=".json",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── ACTION ──────────────────────────────────────────────────────────────────
@register_action("escalate_to_human")
class EscalateToHumanAction(Action):
    """
    Write a structured escalation for human review. No network change.
    """

    # ── 1. Preconditions ───────────────────────────────────────────────
    def preconditions(self, ctx: Dict[str, Any]) -> Tuple[bool, str]:
        # Always applicable — it's the ultimate fallback
        return True, "escalation always permitted"

    # ── 2. Snapshot ────────────────────────────────────────────────────
    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        # Nothing to capture — we don't touch the network
        return {
            "note": "escalation; nothing captured",
            "router": ctx.get("router", ""),
        }

    # ── 3. Execute ─────────────────────────────────────────────────────
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        incident_id = ctx.get("incident_id", "unknown")
        severity = ctx.get("severity", "UNKNOWN")
        router = ctx.get("router", "")
        reason = ctx.get("reason", "no reason provided")
        dry_run = bool(ctx.get("dry_run", False))

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        escalation_id = f"{_safe(incident_id)}_{ts}"
        record = {
            "escalation_id": escalation_id,
            "incident_id": incident_id,
            "severity": severity,
            "root_router": router,
            "reason": reason,
            "created_at": _now_iso(),
            "created_by": ctx.get("actor", "auto"),
            "acknowledged": False,
            "acknowledged_at": None,
            "acknowledged_by": None,
        }

        if dry_run:
            return {
                "executed": True,
                "commands": [],
                "outputs": [],
                "note": f"dry-run: would write escalation {escalation_id}",
                "escalation_id": escalation_id,
                "dry_run": True,
            }

        path = ESCALATIONS_DIR / f"{escalation_id}.json"
        try:
            _atomic_write(path, record)
        except Exception as e:
            return {
                "executed": False,
                "commands": [],
                "outputs": [],
                "note": f"failed to write escalation: {type(e).__name__}: {e}",
                "escalation_id": escalation_id,
                "dry_run": False,
            }

        return {
            "executed": True,
            "commands": [],
            "outputs": [{"path": str(path)}],
            "note": f"escalated {incident_id} on {router or 'network'} for human review",
            "escalation_id": escalation_id,
            "escalation_path": str(path),
            "dry_run": False,
        }

    # ── 4. Verify ──────────────────────────────────────────────────────
    def verify(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Tuple[bool, str]:
        if result.get("dry_run"):
            return True, "dry-run: no verification performed"

        if not result.get("executed"):
            return False, "execute() reported failure"

        path_str = result.get("escalation_path")
        if not path_str:
            return False, "no escalation_path in result"

        path = Path(path_str)
        if not path.exists():
            return False, f"escalation file missing: {path}"

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return False, f"escalation file malformed: {e}"

        if not data.get("escalation_id"):
            return False, "escalation file missing escalation_id"

        return True, f"escalation persisted: {path.name}"

    # ── 5. Rollback ────────────────────────────────────────────────────
    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Escalations are informational records; they should not be deleted
        # on rollback. Humans will see them and dismiss them.
        return {
            "rolled_back": False,
            "note": (
                "escalation is an informational record; "
                "not removed on rollback"
            ),
        }
