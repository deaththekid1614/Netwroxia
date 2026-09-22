#!/usr/bin/env python3
"""
Netwroxia — Phase C5: Rate Limiter Guardrail

Enforces two throttle limits by reading the audit log (C1):

  per_router    : max 1 action per router per 5 minutes (300s)
  network_wide  : max 5 actions across the network per 10 minutes (600s)

"Action" = audit event whose name is in COUNTED_EVENTS:
  action.executed     successful execution
  action.failed       executed but verify failed
  action.rolled_back  reverted

Read-only. No writes. Idempotent. Safe to call from any process.

CLI:
    python3 remediation/guardrails/rate_limiter.py check --router ZO-Bengaluru
    python3 remediation/guardrails/rate_limiter.py status
"""

import argparse
import json
import sys
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
COUNTED_EVENTS = {
    "action.executed",
    "action.failed",
    "action.rolled_back",
}

LIMITS = {
    "per_router":   {"window_seconds": 300, "max_actions": 1},
    "network_wide": {"window_seconds": 600, "max_actions": 5},
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _recent_actions(
    window_seconds: int,
    router: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return audit events from the last N seconds that are counted as actions.
    If router is given, only events for that router.
    Chronologically ordered oldest -> newest.
    """
    out: List[Dict[str, Any]] = []
    for event_name in COUNTED_EVENTS:
        out.extend(audit.recent_events(
            window_seconds=window_seconds,
            event_filter=event_name,
            router=router,
        ))
    # Sort by ts ascending (C1 returns per-event-order across files)
    out.sort(key=lambda e: e.get("ts", ""))
    return out


def _count_actions(
    window_seconds: int,
    router: Optional[str] = None,
) -> int:
    return len(_recent_actions(window_seconds, router))


def _wait_until_slot_frees(
    window_seconds: int,
    router: Optional[str] = None,
) -> int:
    """
    If the limit is saturated, find the oldest counted action and compute
    how many seconds remain before it falls outside the window.
    Returns 0 if no actions exist (should not happen when saturated).
    """
    events = _recent_actions(window_seconds, router)
    if not events:
        return 0
    oldest_ts = _parse_ts(events[0].get("ts"))
    if oldest_ts is None:
        return window_seconds  # conservative fallback

    now = datetime.now(timezone.utc)
    age = (now - oldest_ts).total_seconds()
    remaining = window_seconds - age
    if remaining <= 0:
        return 0
    return int(remaining) + 1  # round up + 1s of slack


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def check(router: Optional[str] = None) -> Dict[str, Any]:
    """
    Check whether an action is permitted right now.

    router=None        -> only network-wide limit is checked
    router="ZO-..."    -> both per-router and network-wide checks run

    Returns a dict with keys:
      allowed, reason, wait_seconds, checks[], timestamp
    """
    now = datetime.now(timezone.utc)
    checks: List[Dict[str, Any]] = []

    # Per-router (only if a specific router is targeted)
    if router is not None:
        cfg = LIMITS["per_router"]
        count = _count_actions(cfg["window_seconds"], router)
        allowed = count < cfg["max_actions"]
        checks.append({
            "name": "per_router",
            "router": router,
            "allowed": allowed,
            "count": count,
            "limit": cfg["max_actions"],
            "window_seconds": cfg["window_seconds"],
            "wait_seconds": (
                0 if allowed
                else _wait_until_slot_frees(cfg["window_seconds"], router)
            ),
        })

    # Network-wide
    cfg = LIMITS["network_wide"]
    count = _count_actions(cfg["window_seconds"], None)
    allowed = count < cfg["max_actions"]
    checks.append({
        "name": "network_wide",
        "router": None,
        "allowed": allowed,
        "count": count,
        "limit": cfg["max_actions"],
        "window_seconds": cfg["window_seconds"],
        "wait_seconds": (
            0 if allowed
            else _wait_until_slot_frees(cfg["window_seconds"], None)
        ),
    })

    all_allowed = all(c["allowed"] for c in checks)
    wait_seconds = 0 if all_allowed else max(c["wait_seconds"] for c in checks)

    if all_allowed:
        reason = "within all rate limits"
    else:
        failures = [c for c in checks if not c["allowed"]]
        reason = "; ".join(
            f"{c['name']} saturated ({c['count']}/{c['limit']})"
            for c in failures
        )

    return {
        "allowed": all_allowed,
        "reason": reason,
        "wait_seconds": wait_seconds,
        "checks": checks,
        "timestamp": now.isoformat(),
    }


def is_throttled(router: Optional[str] = None) -> bool:
    """Convenience: True if an action would be blocked."""
    return not check(router)["allowed"]


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_check(args) -> int:
    result = check(args.router)
    print(json.dumps(result, indent=2))
    return 0 if result["allowed"] else 1


def cmd_status(_args) -> int:
    """Show current counts and remaining capacity for both limits."""
    now_str = datetime.now(timezone.utc).isoformat()
    print("=" * 72)
    print(" NETWROXIA RATE LIMITER STATUS")
    print("=" * 72)
    print(f" Timestamp: {now_str}")
    print("-" * 72)

    for name, cfg in LIMITS.items():
        count = _count_actions(cfg["window_seconds"], None)
        remaining = max(0, cfg["max_actions"] - count)
        status = "OK" if remaining > 0 else "SATURATED"
        print(f" {name:14s}  window={cfg['window_seconds']:4d}s  "
              f"count={count}/{cfg['max_actions']}  "
              f"remaining={remaining}  [{status}]")

    print("-" * 72)
    # Per-router breakdown of the last network window
    window = LIMITS["network_wide"]["window_seconds"]
    events = _recent_actions(window, None)
    if events:
        print(f" Actions in last {window}s: {len(events)}")
        by_router: Dict[str, int] = {}
        for e in events:
            r = e.get("router") or "(none)"
            by_router[r] = by_router.get(r, 0) + 1
        for r, n in sorted(by_router.items()):
            print(f"   {r:24s}  {n}")
    else:
        print(" No counted actions in window.")
    print("=" * 72)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Rate Limiter Guardrail (C5)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Check if action would be allowed")
    p_check.add_argument("--router", default=None,
                         help="Router name; omit for network-wide-only check")

    sub.add_parser("status", help="Show current throttle status")

    args = parser.parse_args()
    if args.command == "check":
        sys.exit(cmd_check(args))
    if args.command == "status":
        sys.exit(cmd_status(args))


if __name__ == "__main__":
    main()
