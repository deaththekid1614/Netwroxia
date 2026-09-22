#!/usr/bin/env python3
"""
Netwroxia — Phase C8: Action Base Class

Abstract contract for all remediation actions. Defines 5 methods:
  preconditions(ctx)          -> (bool, reason)     : is action applicable?
  snapshot(ctx)               -> dict               : capture pre-state
  execute(ctx)                -> dict               : do the thing
  verify(ctx, snapshot, res)  -> (bool, reason)     : did it work?
  rollback(ctx, snapshot)     -> dict               : undo on verify fail

Also provides:
  - ACTIONS registry + register_action decorator
  - get_action(name) lookup
  - run_in_container() helper (no shell=True anywhere)
  - resolve_container() short -> full name

Executor (C14) drives the lifecycle. Actions never call each other.

CLI:
    python3 remediation/actions/_base.py list
    python3 remediation/actions/_base.py info restart_bgp
"""

import json
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from audit import audit_logger as audit  # noqa: F401 (used by subclasses)

# ── CONSTANTS ───────────────────────────────────────────────────────────────
# Short router name -> full containerlab container name
CONTAINER_MAP = {
    "HO-Chennai":     "clab-netwroxia-ho-chennai",
    "ZO-Bengaluru":   "clab-netwroxia-zo-bengaluru",
    "BR-Koramangala": "clab-netwroxia-br-koramangala",
    "BR-Whitefield":  "clab-netwroxia-br-whitefield",
}

DEFAULT_EXEC_TIMEOUT = 15
DEFAULT_VERIFY_TIMEOUT = 60

# ── REGISTRY ────────────────────────────────────────────────────────────────
ACTIONS: Dict[str, type] = {}


def register_action(name: str):
    """Decorator to register an Action subclass under a name."""
    def decorator(cls):
        if name in ACTIONS:
            raise ValueError(f"action already registered: {name}")
        cls.ACTION_NAME = name
        ACTIONS[name] = cls
        return cls
    return decorator


def get_action(name: str) -> Optional["Action"]:
    """Return a fresh instance of the named action, or None."""
    cls = ACTIONS.get(name)
    return cls() if cls else None


def list_actions() -> list:
    return sorted(ACTIONS.keys())


# ── SHARED HELPERS ──────────────────────────────────────────────────────────
def resolve_container(router: str) -> str:
    """
    Map short router name (ZO-Bengaluru) to full container name.
    Pass-through if already prefixed with clab-netwroxia-.
    """
    if not router:
        raise ValueError("router is required")
    if router in CONTAINER_MAP:
        return CONTAINER_MAP[router]
    if router.startswith("clab-netwroxia-"):
        return router
    raise ValueError(f"unknown router: {router}")


def run_in_container(
    container: str,
    cmd: str,
    timeout: int = DEFAULT_EXEC_TIMEOUT,
) -> Tuple[int, str, str]:
    """
    Execute a shell command inside a container. NO shell=True at this layer.
    The command string is passed to `sh -c` inside the container, which is
    the caller's responsibility — do not interpolate untrusted user input.

    Returns (returncode, stdout, stderr). Never raises on normal failures.
    """
    full_cmd = ["docker", "exec", container, "sh", "-c", cmd]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", "docker binary not found"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"
    return result.returncode, result.stdout or "", result.stderr or ""


# ── ABSTRACT ACTION ─────────────────────────────────────────────────────────
class Action(ABC):
    """
    Base class for all remediation actions.

    Subclasses must set ACTION_NAME via the @register_action decorator
    and implement at least `execute()`.
    """

    ACTION_NAME: str = "unnamed"

    # ── 1. Preconditions ───────────────────────────────────────────────
    def preconditions(self, ctx: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Return (allowed, reason). Default: always allowed.
        Override to gate on context (e.g. dry_run, router role).
        """
        return True, "always applicable"

    # ── 2. Snapshot ────────────────────────────────────────────────────
    def snapshot(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Capture pre-action state for rollback. Default: fetch the router's
        running config via vtysh. Subclasses may extend or replace.
        """
        router = ctx.get("router", "")
        container = ctx.get("container") or resolve_container(router)
        rc, out, err = run_in_container(
            container, "vtysh -c 'show running-config' 2>/dev/null",
            timeout=DEFAULT_EXEC_TIMEOUT,
        )
        return {
            "router": router,
            "container": container,
            "vtysh_rc": rc,
            "running_config": out[:50000],  # cap to keep snapshots small
            "running_config_err": err[:2000],
            "captured_at": _now_iso(),
        }

    # ── 3. Execute ─────────────────────────────────────────────────────
    @abstractmethod
    def execute(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform the action. Must return a dict with at least:
          {"executed": bool, "commands": [str, ...], "outputs": [...]}
        Do not raise on command failure — return executed=False instead.
        """
        raise NotImplementedError

    # ── 4. Verify ──────────────────────────────────────────────────────
    def verify(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Return (verified, reason). Default: no verification, always True.
        Override for real checks (peer count, adjacency count, beacon state).
        """
        return True, "no verification defined"

    # ── 5. Rollback ────────────────────────────────────────────────────
    def rollback(
        self,
        ctx: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Undo the action using the snapshot. Default: log-only, no change.
        Return {"rolled_back": bool, "note": str}.
        """
        return {
            "rolled_back": False,
            "note": "no rollback defined for this action",
        }


# ── UTILITY ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_list(_args) -> int:
    names = list_actions()
    if not names:
        print("(no actions registered)")
        return 0
    print(f"[LIST] {len(names)} action(s) registered")
    for name in names:
        cls = ACTIONS[name]
        doc = (cls.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else "(no description)"
        print(f"  {name:24s}  {summary}")
    return 0


def cmd_info(args) -> int:
    cls = ACTIONS.get(args.action)
    if cls is None:
        print(f"[MISS] action not registered: {args.action}")
        return 1
    info = {
        "action": cls.ACTION_NAME,
        "class": cls.__name__,
        "has_custom_preconditions": cls.preconditions is not Action.preconditions,
        "has_custom_snapshot": cls.snapshot is not Action.snapshot,
        "has_custom_verify": cls.verify is not Action.verify,
        "has_custom_rollback": cls.rollback is not Action.rollback,
        "doc": (cls.__doc__ or "").strip(),
    }
    print(json.dumps(info, indent=2))
    return 0


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Netwroxia Action Base (C8)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list")

    p_info = sub.add_parser("info")
    p_info.add_argument("action")

    args = parser.parse_args()
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "info":
        sys.exit(cmd_info(args))


# ── AUTO-DISCOVERY ──────────────────────────────────────────────────────────
def _autodiscover():
    """
    Import sibling action modules so they register themselves.
    Called at module load. Skips files starting with `_` (like this one).
    Failures are non-fatal — a broken action shouldn't crash the CLI.
    """
    import importlib
    import pkgutil

    pkg_dir = Path(__file__).resolve().parent
    for mod_info in pkgutil.iter_modules([str(pkg_dir)]):
        name = mod_info.name
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"remediation.actions.{name}")
        except Exception as e:
            print(f"[WARN] could not import action module '{name}': "
                  f"{type(e).__name__}: {e}", file=sys.stderr)


if __name__ == "__main__":
    # When run as a script, alias __main__ under the canonical module name
    # so that sibling action modules importing `remediation.actions._base`
    # resolve to THIS module object (shared registry), not a fresh copy.
    sys.modules.setdefault("remediation.actions._base", sys.modules["__main__"])
    _autodiscover()
    main()
else:
    _autodiscover()
