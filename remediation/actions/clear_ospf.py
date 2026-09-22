#!/usr/bin/env python3
"""
Netwroxia — Phase C11: Clear OSPF Action

Restarts the OSPF process on a target router via
`vtysh clear ip ospf process`. OSPF adjacencies re-form automatically.
Verify polls until FULL neighbor count returns to baseline.

Safety: refuses HO-Chennai unless ctx["allow_ho"] is True. Clearing
HO's OSPF breaks ZO's route to HO's loopback, cascading downstream.

Registered as: "clear_ospf"
"""

import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
EXPECTED_NEIGHBORS = {
    "HO-Chennai":     1,
    "ZO-Bengaluru":   3,
    "BR-Koramangala": 1,
    "BR-Whitefield":  1,
}

VERIFY_POLL_INTERVAL = 5
VERIFY_TIMEOUT = 60

# FRR emits lines like:
#   10.255.0.2   1   Full/DR         00:01:23   10.0.1.2    eth1:10.0.1.1
#   10.255.0.3   1   Full/Backup     00:00:45   10.1.2.2    eth2:10.1.2.1
# State field is column 3; we accept any line whose state part starts with Full
NEIGHBOR_LINE_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+)\s+(\S+)")


# ── PARSING HELPERS ─────────────────────────────────────────────────────────
def parse_ospf_neighbors(stdout: str) -> Dict[str, Any]:
    """
    Parse `show ip ospf neighbor` output.

    Returns:
      {
        "total": int,              # neighbor lines found
        "full": int,               # lines whose state starts with "Full"
        "neighbors": [
          {"id": "10.255.0.2", "state": "Full/DR"},
          ...
        ]
      }
    """
    neighbors: List[Dict[str, str]] = []
    for line in stdout.splitlines():
        m = NEIGHBOR_LINE_RE.match(line)
        if not m:
            continue
        neighbors.append({
            "id": m.group(1),
            "state": m.group(3),
        })

    full = sum(1 for n in neighbors if n["state"].lower().startswith("full"))
    return {
        "total": len(neighbors),
        "full": full,
        "neighbors": neighbors,
    }


def _fetch_ospf_state(container: str) -> Dict[str, Any]:
    rc, out, err = run_in_container(
        container,
        'vtysh -c "show ip ospf neighbor" 2>/dev/null',
        timeout=15,
    )
    parsed = parse_ospf_neighbors(out)
    parsed["vtysh_rc"] = rc
    parsed["vtysh_err"] = (err or "").strip()[:500]
    return parsed


# ── ACTION ──────────────────────────────────────────────────────────────────
@register_action("clear_ospf")
class ClearOSPFAction(Action):
    """
    Restart OSPF process on the target router. Adjacencies re-form.
    Safe for branch and zonal routers. Refuses HO unless explicitly allowed.
    """

    # ── 1. Preconditions ───────────────────────────────────────────────
    def preconditions(self, ctx: Dict[str, Any]) -> Tuple[bool, str]:
        router = ctx.get("router", "")
        if not router:
            return False, "no router specified"

        if router == "HO-Chennai" and not ctx.get("allow_ho", False):
            return False, (
                "refusing to clear OSPF on HO-Chennai; cascading effect "
                "on all downstream routers. Set ctx['allow_ho']=True to override."
            )

        try:
            container = resolve_container(router)
        except ValueError as e:
            return False, str(e)

        rc, _out, err = run_in_container(container, "true", timeout=5)
        if rc != 0:
            return False, f"container unreachable: {err.strip() or rc}"

        return True, f"OSPF restart permitted on {router}"

    # ── 2. Snapshot ────────────────────────────────────────────────────
    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        ospf = _fetch_ospf_state(container)
        return {
            "router": router,
            "container": container,
            "pre_ospf": ospf,
            "expected_neighbors": EXPECTED_NEIGHBORS.get(
                router, ospf["full"]
            ),
            "captured_at": _now_iso(),
        }

    # ── 3. Execute ─────────────────────────────────────────────────────
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        dry_run = bool(ctx.get("dry_run", False))

        cmd = 'vtysh -c "clear ip ospf process"'

        if dry_run:
            return {
                "executed": True,
                "commands": [cmd],
                "outputs": [],
                "note": f"dry-run: would clear OSPF on {router}",
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
        expected = int(snapshot.get("expected_neighbors",
                                    EXPECTED_NEIGHBORS.get(router, 1)))

        if result.get("dry_run"):
            return True, "dry-run: no verification performed"

        if not result.get("executed"):
            return False, "execute() reported failure — not verifying"

        deadline = time.time() + VERIFY_TIMEOUT
        last_full = -1
        last_neighbors: List[Dict[str, str]] = []

        while time.time() < deadline:
            ospf = _fetch_ospf_state(container)
            last_full = ospf["full"]
            last_neighbors = ospf["neighbors"]

            if last_full >= expected:
                return True, (
                    f"OSPF recovered: {last_full}/{expected} FULL on {router}"
                )
            time.sleep(VERIFY_POLL_INTERVAL)

        return False, (
            f"OSPF did not recover within {VERIFY_TIMEOUT}s on {router}: "
            f"{last_full}/{expected} FULL, neighbors={last_neighbors}"
        )

    # ── 5. Rollback ────────────────────────────────────────────────────
    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "rolled_back": False,
            "note": (
                "OSPF restart is self-recovering; no manual rollback. "
                "If verify timed out, escalate for human review."
            ),
        }


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
