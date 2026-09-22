#!/usr/bin/env python3
"""
Netwroxia — Phase C10: Restart BGP Action

Clears BGP sessions on a target router via `vtysh clear ip bgp *`.
BGP re-establishes automatically. Verify polls until peer count
returns to the baseline captured in snapshot.

Safety: refuses HO-Chennai unless ctx["allow_ho"] is True. HO is the
route reflector; clearing it affects the entire network.

Registered as: "restart_bgp"
"""

import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
EXPECTED_PEERS = {
    "HO-Chennai":     3,
    "ZO-Bengaluru":   1,
    "BR-Koramangala": 1,
    "BR-Whitefield":  1,
}

VERIFY_POLL_INTERVAL = 5
VERIFY_TIMEOUT = 60

# Lines containing any of these are considered NOT established
DOWN_STATES = ("Active", "Idle", "Connect", "OpenSent", "OpenConfirm")

# Matches lines like: 10.255.0.2  4  65001  ...
PEER_LINE_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\d+\s+\d+")


# ── PARSING HELPERS ─────────────────────────────────────────────────────────
def parse_bgp_summary(stdout: str) -> Dict[str, Any]:
    """
    Parse `show ip bgp summary` output into a peer-count summary.
    Returns:
      {
        "total_peers": int,
        "established": int,
        "down": int,
        "peers": [{"ip": "10.255.0.2", "state": "Established"|"Active"|...}]
      }
    """
    peers: List[Dict[str, str]] = []

    for line in stdout.splitlines():
        m = PEER_LINE_RE.match(line)
        if not m:
            continue
        ip = m.group(1)
        # Determine state by scanning for any DOWN_STATES keyword
        state = "Established"  # default for a peer line without down state
        for ds in DOWN_STATES:
            if ds in line:
                state = ds
                break
        peers.append({"ip": ip, "state": state})

    established = sum(1 for p in peers if p["state"] == "Established")
    return {
        "total_peers": len(peers),
        "established": established,
        "down": len(peers) - established,
        "peers": peers,
    }


def _fetch_bgp_state(container: str) -> Dict[str, Any]:
    """Run show ip bgp summary inside the container and parse the result."""
    rc, out, err = run_in_container(
        container,
        'vtysh -c "show ip bgp summary" 2>/dev/null',
        timeout=15,
    )
    parsed = parse_bgp_summary(out)
    parsed["vtysh_rc"] = rc
    parsed["vtysh_err"] = (err or "").strip()[:500]
    return parsed


# ── ACTION ──────────────────────────────────────────────────────────────────
@register_action("restart_bgp")
class RestartBGPAction(Action):
    """
    Clear BGP sessions on the target router. BGP re-establishes itself.
    Safe for branch and zonal routers. Refuses HO unless explicitly allowed.
    """

    # ── 1. Preconditions ───────────────────────────────────────────────
    def preconditions(self, ctx: Dict[str, Any]) -> Tuple[bool, str]:
        router = ctx.get("router", "")
        if not router:
            return False, "no router specified"

        # Refuse HO unless explicitly allowed
        if router == "HO-Chennai" and not ctx.get("allow_ho", False):
            return False, (
                "refusing to clear BGP on HO-Chennai "
                "(route reflector). Set ctx['allow_ho']=True to override."
            )

        # Container must exist / be reachable
        try:
            container = resolve_container(router)
        except ValueError as e:
            return False, str(e)

        rc, _out, err = run_in_container(container, "true", timeout=5)
        if rc != 0:
            return False, f"container unreachable: {err.strip() or rc}"

        return True, f"BGP restart permitted on {router}"

    # ── 2. Snapshot ────────────────────────────────────────────────────
    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        bgp = _fetch_bgp_state(container)
        return {
            "router": router,
            "container": container,
            "pre_bgp": bgp,
            "expected_peers": EXPECTED_PEERS.get(router, bgp["total_peers"]),
            "captured_at": _now_iso(),
        }

    # ── 3. Execute ─────────────────────────────────────────────────────
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        dry_run = bool(ctx.get("dry_run", False))

        cmd = 'vtysh -c "clear ip bgp *"'

        if dry_run:
            return {
                "executed": True,
                "commands": [cmd],
                "outputs": [],
                "note": f"dry-run: would clear BGP on {router}",
                "dry_run": True,
            }

        rc, out, err = run_in_container(container, cmd, timeout=15)
        return {
            "executed": rc == 0,
            "commands": [cmd],
            "outputs": [{"rc": rc, "stdout": out.strip()[:1000],
                         "stderr": err.strip()[:1000]}],
            "router": router,
            "container": container,
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
        expected = int(snapshot.get("expected_peers",
                                    EXPECTED_PEERS.get(router, 1)))

        if result.get("dry_run"):
            return True, "dry-run: no verification performed"

        if not result.get("executed"):
            return False, "execute() reported failure — not verifying"

        deadline = time.time() + VERIFY_TIMEOUT
        last_established = -1
        last_peers: List[Dict[str, str]] = []

        while time.time() < deadline:
            bgp = _fetch_bgp_state(container)
            last_established = bgp["established"]
            last_peers = bgp["peers"]

            if last_established >= expected:
                return True, (
                    f"BGP recovered: {last_established}/{expected} "
                    f"established on {router}"
                )
            time.sleep(VERIFY_POLL_INTERVAL)

        # Timeout: report what we saw
        return False, (
            f"BGP did not recover within {VERIFY_TIMEOUT}s on {router}: "
            f"{last_established}/{expected} established, "
            f"peers={last_peers}"
        )

    # ── 5. Rollback ────────────────────────────────────────────────────
    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        No-op. BGP self-recovers by design. We do not manually re-inject
        sessions — that would race with FRR's own convergence.
        """
        return {
            "rolled_back": False,
            "note": (
                "BGP restart is self-recovering; no manual rollback. "
                "If verify timed out, escalate for human review."
            ),
        }


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
