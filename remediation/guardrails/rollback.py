#!/usr/bin/env python3
"""
Netwroxia — Phase C7: Rollback Manager (Snapshots)

Storage layer for pre-action router snapshots. C14 executor calls
save_snapshot(...) before executing any action, and get_latest_snapshot()
to retrieve pre-state if verification fails.

This module does NOT touch the network. Pure file I/O.

Storage: incidents/snapshots/<incident_id>_<action>_<ts>.json
Override: NETWROXIA_SNAPSHOTS_DIR env var (for tests)

CLI:
    python3 remediation/guardrails/rollback.py save \\
        --incident-id INC-X --action restart_bgp --router ZO-Bengaluru \\
        --state '{"bgp_peer_count": 3}'
    python3 remediation/guardrails/rollback.py list [--incident-id X] [--action Y]
    python3 remediation/guardrails/rollback.py latest \\
        --incident-id INC-X --action restart_bgp
    python3 remediation/guardrails/rollback.py mark-restored \\
        --snapshot-id <id> --actor death-kid --note "verify failed"
"""

import argparse
import json
import os
import re
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
SNAPSHOTS_DIR = Path(os.environ.get(
    "NETWROXIA_SNAPSHOTS_DIR",
    str(PROJECT_ROOT / "incidents" / "snapshots"),
))

SCHEMA_VERSION = 1

# Only letters, digits, dot, dash, underscore in IDs/actions
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(s: str) -> str:
    """Replace unsafe chars with underscore. Keeps filenames portable."""
    return _SAFE_RE.sub("_", s or "unknown")


def _make_snapshot_id(incident_id: str, action: str) -> str:
    """Compose a unique filename-friendly snapshot id."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"{_sanitize(incident_id)}_{_sanitize(action)}_{ts}"
    # Collision guard — add microsecond suffix if the base already exists
    if (SNAPSHOTS_DIR / f"{base}.json").exists():
        micros = datetime.now(timezone.utc).strftime("%f")[:6]
        base = f"{base}_{micros}"
    return base


def _path_for(snapshot_id: str) -> Path:
    return SNAPSHOTS_DIR / f"{_sanitize(snapshot_id)}.json"


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".snap_", suffix=".json", dir=str(path.parent),
    )
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


def _read_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def save_snapshot(
    incident_id: str,
    action: str,
    router: str,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Persist a snapshot. Returns the written record (with snapshot_id,
    created_at, restored_at=None).

    Caller decides what goes in `state` — typically a router pre-state
    blob from vtysh or a tc/qdisc dump.
    """
    if not incident_id:
        raise ValueError("incident_id is required")
    if not action:
        raise ValueError("action is required")
    if not isinstance(state, dict):
        raise ValueError("state must be a dict")

    snapshot_id = _make_snapshot_id(incident_id, action)
    record = {
        "version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "incident_id": incident_id,
        "action": action,
        "router": router or "",
        "created_at": _now_iso(),
        "state": state,
        "restored_at": None,
        "restored_by": None,
        "restore_note": "",
    }
    _atomic_write(_path_for(snapshot_id), record)

    audit.log(
        event="snapshot.saved",
        incident_id=incident_id,
        router=router,
        actor="auto",
        data={"snapshot_id": snapshot_id, "action": action,
              "state_keys": sorted(state.keys())},
    )
    return record


def get_snapshot(snapshot_id: str) -> Optional[Dict[str, Any]]:
    if not snapshot_id:
        return None
    return _read_snapshot(_path_for(snapshot_id))


def get_latest_snapshot(
    incident_id: str,
    action: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return the most recent snapshot for an incident (optionally filtered
    by action). Returns None if nothing matches.
    """
    if not incident_id:
        return None
    matches = list_snapshots(incident_id=incident_id, action=action, limit=1)
    return matches[0] if matches else None


def list_snapshots(
    incident_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Return snapshots sorted newest-first. Filters are exact-match.
    Reads every file each call; fine for the volumes Netwroxia produces.
    """
    if not SNAPSHOTS_DIR.exists():
        return []

    out: List[Dict[str, Any]] = []
    for path in SNAPSHOTS_DIR.glob("*.json"):
        rec = _read_snapshot(path)
        if rec is None:
            continue
        if incident_id is not None and rec.get("incident_id") != incident_id:
            continue
        if action is not None and rec.get("action") != action:
            continue
        out.append(rec)

    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return out[:limit]


def mark_restored(
    snapshot_id: str,
    actor: str = "auto",
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Mark a snapshot as restored. Idempotent by default: if already marked,
    returns the existing record unchanged. Pass force=True to overwrite
    the restored_at timestamp.
    """
    path = _path_for(snapshot_id)
    rec = _read_snapshot(path)
    if rec is None:
        raise ValueError(f"snapshot not found: {snapshot_id}")

    if rec.get("restored_at") is not None and not force:
        return rec

    rec["restored_at"] = _now_iso()
    rec["restored_by"] = actor
    rec["restore_note"] = note
    _atomic_write(path, rec)

    audit.log(
        event="snapshot.restored",
        incident_id=rec.get("incident_id"),
        router=rec.get("router"),
        actor=actor,
        data={"snapshot_id": snapshot_id, "action": rec.get("action"),
              "note": note},
    )
    return rec


def delete_snapshot(snapshot_id: str) -> bool:
    path = _path_for(snapshot_id)
    if not path.exists():
        return False
    path.unlink()
    return True


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_save(args) -> int:
    try:
        state = json.loads(args.state)
    except json.JSONDecodeError as e:
        print(f"[ERROR] --state is not valid JSON: {e}")
        return 1
    rec = save_snapshot(
        incident_id=args.incident_id,
        action=args.action,
        router=args.router,
        state=state,
    )
    print(json.dumps(rec, indent=2))
    return 0


def cmd_list(args) -> int:
    recs = list_snapshots(
        incident_id=args.incident_id,
        action=args.action,
        limit=args.limit,
    )
    if not recs:
        print("(no snapshots)")
        return 0
    print(f"[LIST] {len(recs)} snapshot(s)")
    for r in recs:
        restored = "RESTORED" if r.get("restored_at") else "live"
        print(f"  {r.get('snapshot_id', '?'):60s}  "
              f"inc={r.get('incident_id', '?'):20s}  "
              f"action={r.get('action', '?'):18s}  "
              f"router={r.get('router', '?'):16s}  "
              f"[{restored}]")
    return 0


def cmd_latest(args) -> int:
    rec = get_latest_snapshot(args.incident_id, args.action)
    if rec is None:
        print("(no snapshot)")
        return 1
    print(json.dumps(rec, indent=2))
    return 0


def cmd_mark_restored(args) -> int:
    try:
        rec = mark_restored(
            snapshot_id=args.snapshot_id,
            actor=args.actor,
            note=args.note,
            force=args.force,
        )
    except ValueError as e:
        print(f"[ERROR] {e}")
        return 1
    print(json.dumps(rec, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Rollback Manager (C7)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save")
    p_save.add_argument("--incident-id", required=True)
    p_save.add_argument("--action", required=True)
    p_save.add_argument("--router", default="")
    p_save.add_argument("--state", required=True,
                        help='JSON dict, e.g. \'{"x": 1}\'')

    p_list = sub.add_parser("list")
    p_list.add_argument("--incident-id", default=None)
    p_list.add_argument("--action", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_latest = sub.add_parser("latest")
    p_latest.add_argument("--incident-id", required=True)
    p_latest.add_argument("--action", default=None)

    p_mark = sub.add_parser("mark-restored")
    p_mark.add_argument("--snapshot-id", required=True)
    p_mark.add_argument("--actor", default="cli")
    p_mark.add_argument("--note", default="")
    p_mark.add_argument("--force", action="store_true")

    args = parser.parse_args()
    if args.command == "save":
        sys.exit(cmd_save(args))
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "latest":
        sys.exit(cmd_latest(args))
    if args.command == "mark-restored":
        sys.exit(cmd_mark_restored(args))


if __name__ == "__main__":
    main()
