#!/usr/bin/env python3
"""
Netwroxia — Phase C9: No-Op Action

Monitoring-only action. Used when the decision tree chooses to watch
without intervention (NONE/LOW severity, or explicit "hold" decisions).

Does not touch the network. Execute returns success. Snapshot is empty.
Verify and rollback are trivial no-ops.

Registered as: "no_op"
"""

import sys
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


# ── ACTION ──────────────────────────────────────────────────────────────────
@register_action("no_op")
class NoOpAction(Action):
    """
    Monitoring-only. Intentionally does nothing to the network.
    Chosen when no automated intervention is warranted.
    """

    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Override: no state to capture for a no-op. Avoids unnecessary
        vtysh reads and keeps the snapshot store clean.
        """
        return {
            "note": "no-op; nothing captured",
            "router": ctx.get("router", ""),
        }

    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Do nothing. Report success with a monitoring note.
        """
        router = ctx.get("router", "")
        reason = ctx.get("reason", "monitoring only")
        return {
            "executed": True,
            "commands": [],
            "outputs": [],
            "note": f"no-op on {router or 'network'}: {reason}",
            "dry_run": bool(ctx.get("dry_run", False)),
        }

    def verify(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """No state change to verify."""
        return True, "no-op requires no verification"

    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """No state change to roll back."""
        return {
            "rolled_back": False,
            "note": "no-op has nothing to roll back",
        }
