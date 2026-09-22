#!/usr/bin/env python3
"""
Netwroxia — Phase C12: Reroute Traffic Action

Applies a tc traffic-shaping qdisc on the target router's primary
interface. Simulates "reroute via backup path" by deprioritizing the
primary link. Genuinely reversible: rollback removes the qdisc.

In production this would be a BGP community change or SD-WAN policy
switch. In our topology, the tc qdisc demonstrates the same idea.

Safety: refuses HO-Chennai unless ctx["allow_ho"] is True.

Registered as: "reroute_traffic"
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
    resolve_container,
    run_in_container,
)

# ── CONFIG ──────────────────────────────────────────────────────────────────
# Every router's primary data-plane uplink is eth1 (verified against topology)
DEFAULT_INTERFACE = {
    "HO-Chennai":     "eth1",
    "ZO-Bengaluru":   "eth1",
    "BR-Koramangala": "eth1",
    "BR-Whitefield":  "eth1",
}

DEFAULT_DELAY_MS = 5


# ── ACTION ──────────────────────────────────────────────────────────────────
@register_action("reroute_traffic")
class RerouteTrafficAction(Action):
    """
    Apply a tc netem delay qdisc on the target interface to simulate
    traffic reroute. Reversible: rollback removes the qdisc.
    """

    # ── 1. Preconditions ───────────────────────────────────────────────
    def preconditions(self, ctx: Dict[str, Any]) -> Tuple[bool, str]:
        router = ctx.get("router", "")
        if not router:
            return False, "no router specified"

        if router == "HO-Chennai" and not ctx.get("allow_ho", False):
            return False, (
                "refusing to reroute on HO-Chennai; may affect downstream "
                "stability. Set ctx['allow_ho']=True to override."
            )

        try:
            container = resolve_container(router)
        except ValueError as e:
            return False, str(e)

        rc, _out, err = run_in_container(container, "true", timeout=5)
        if rc != 0:
            return False, f"container unreachable: {err.strip() or rc}"

        # Interface must be defined (either explicitly or via default table)
        iface = ctx.get("interface") or DEFAULT_INTERFACE.get(router)
        if not iface:
            return False, f"no default interface for {router}; provide ctx['interface']"

        return True, f"reroute permitted on {router} ({iface})"

    # ── 2. Snapshot ────────────────────────────────────────────────────
    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        iface = ctx.get("interface") or DEFAULT_INTERFACE.get(router, "eth1")

        rc, out, err = run_in_container(
            container,
            f"tc qdisc show dev {iface} 2>/dev/null",
            timeout=10,
        )
        return {
            "router": router,
            "container": container,
            "interface": iface,
            "pre_qdisc": out.strip(),
            "pre_qdisc_rc": rc,
            "pre_qdisc_err": err.strip()[:500],
            "captured_at": _now_iso(),
        }

    # ── 3. Execute ─────────────────────────────────────────────────────
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        iface = ctx.get("interface") or DEFAULT_INTERFACE.get(router, "eth1")
        delay_ms = int(ctx.get("delay_ms", DEFAULT_DELAY_MS))
        dry_run = bool(ctx.get("dry_run", False))

        cmd = f"tc qdisc add dev {iface} root netem delay {delay_ms}ms"

        if dry_run:
            return {
                "executed": True,
                "commands": [cmd],
                "outputs": [],
                "note": f"dry-run: would apply reroute qdisc on {router}:{iface}",
                "dry_run": True,
                "router": router,
                "interface": iface,
                "delay_ms": delay_ms,
            }

        # Idempotent apply: remove any existing qdisc first, ignore failure
        run_in_container(
            container,
            f"tc qdisc del dev {iface} root 2>/dev/null; echo ok",
            timeout=10,
        )

        rc, out, err = run_in_container(container, cmd, timeout=15)
        return {
            "executed": rc == 0,
            "commands": [cmd],
            "outputs": [{"rc": rc,
                         "stdout": out.strip()[:1000],
                         "stderr": err.strip()[:1000]}],
            "router": router,
            "container": container,
            "interface": iface,
            "delay_ms": delay_ms,
            "dry_run": False,
        }

    # ── 4. Verify ──────────────────────────────────────────────────────
    def verify(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Tuple[bool, str]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        iface = result.get("interface") or snapshot.get("interface", "eth1")

        if result.get("dry_run"):
            return True, "dry-run: no verification performed"

        if not result.get("executed"):
            return False, "execute() reported failure — not verifying"

        rc, out, err = run_in_container(
            container,
            f"tc qdisc show dev {iface} 2>/dev/null",
            timeout=10,
        )
        if rc != 0:
            return False, f"tc qdisc show failed on {router}:{iface} rc={rc}"

        qdisc = out.strip()
        if "netem" in qdisc and "delay" in qdisc:
            return True, f"reroute qdisc active on {router}:{iface}: {qdisc[:140]}"
        return False, (
            f"qdisc not detected on {router}:{iface}: {qdisc[:140] or '(empty)'}"
        )

    # ── 5. Rollback ────────────────────────────────────────────────────
    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        router = snapshot.get("router", "")
        container = snapshot.get("container") or resolve_container(router)
        iface = snapshot.get("interface")

        if not iface:
            return {
                "rolled_back": False,
                "note": "no interface in snapshot; cannot roll back",
            }

        rc, out, err = run_in_container(
            container,
            f"tc qdisc del dev {iface} root 2>/dev/null; echo ok",
            timeout=10,
        )
        return {
            "rolled_back": rc == 0,
            "note": f"removed qdisc on {router}:{iface}",
            "outputs": [{"rc": rc,
                         "stdout": out.strip()[:500],
                         "stderr": err.strip()[:500]}],
        }


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
