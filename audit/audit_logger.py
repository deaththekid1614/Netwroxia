#!/usr/bin/env python3
"""
Netwroxia — Phase C1: Audit Logger

Append-only JSONL audit log. Every action, state change, and guardrail
decision passes through here. One file per day.

Atomic writes: uses fcntl.flock for POSIX exclusive lock. Concurrent
processes serialize cleanly — no partial lines, no interleaved writes.

Schema (one JSON object per line):
  {
    "ts":         ISO-8601 UTC with microseconds,
    "event":      dotted name, e.g. "action.executed",
    "incident_id": string or null,
    "router":     string or null,
    "actor":      "system" or "auto" or "human:<name>",
    "data":       free-form dict
  }

CLI:
    python3 audit/audit_logger.py log     --event test.smoke --router HO-Chennai
    python3 audit/audit_logger.py tail    --n 20
    python3 audit/audit_logger.py count   --event action.executed --window 600
"""

import argparse
import fcntl
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "audit" / "audit_logs"


# ── CORE ────────────────────────────────────────────────────────────────────
def _today_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"{day}.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(
    event: str,
    incident_id: Optional[str] = None,
    router: Optional[str] = None,
    actor: str = "system",
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Append one event to today's log file.

    Returns the written record (with ts). Safe for concurrent processes.
    """
    if not event or not isinstance(event, str):
        raise ValueError("event must be a non-empty string")

    record: Dict[str, Any] = {
        "ts": _now_iso(),
        "event": event,
        "incident_id": incident_id,
        "router": router,
        "actor": actor,
        "data": data or {},
    }

    line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
    path = _today_file()

    # Atomic append with exclusive lock
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    return record


# ── QUERY HELPERS ───────────────────────────────────────────────────────────
def _iter_all_records() -> List[Dict[str, Any]]:
    """Read all records from all daily files, oldest first."""
    if not LOG_DIR.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Skip corrupt line but keep going — never crash the caller
                        continue
        except OSError:
            continue
    return out


def _parse_ts(record: Dict[str, Any]) -> Optional[datetime]:
    ts = record.get("ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def recent_events(
    window_seconds: int = 300,
    event_filter: Optional[str] = None,
    router: Optional[str] = None,
    incident_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Return events within the last N seconds that match the filters.

    Filters are exact-match by default. `event_filter` can be a prefix
    (e.g. "action." matches "action.executed" and "action.failed").
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    out: List[Dict[str, Any]] = []

    for rec in _iter_all_records():
        ts = _parse_ts(rec)
        if ts is None or ts < cutoff:
            continue
        if event_filter is not None:
            ev = rec.get("event", "")
            if not (ev == event_filter or ev.startswith(event_filter)):
                continue
        if router is not None and rec.get("router") != router:
            continue
        if incident_id is not None and rec.get("incident_id") != incident_id:
            continue
        out.append(rec)

    return out


def count_recent(
    event_filter: str,
    window_seconds: int = 300,
    router: Optional[str] = None,
) -> int:
    return len(recent_events(
        window_seconds=window_seconds,
        event_filter=event_filter,
        router=router,
    ))


def tail(n: int = 20) -> List[Dict[str, Any]]:
    """Return the last N records across all daily files."""
    all_recs = _iter_all_records()
    return all_recs[-n:] if n > 0 else []


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_log(args) -> int:
    rec = log(
        event=args.event,
        incident_id=args.incident_id,
        router=args.router,
        actor=args.actor,
        data={"source": "cli"},
    )
    print(f"[OK] Wrote: {rec['ts']}  {rec['event']}")
    return 0


def cmd_tail(args) -> int:
    recs = tail(args.n)
    if not recs:
        print("(no records)")
        return 0
    for r in recs:
        inc = r.get("incident_id") or "-"
        rtr = r.get("router") or "-"
        print(f"{r['ts']}  {r['event']:<28s}  inc={inc:<24s}  rtr={rtr}")
    return 0


def cmd_count(args) -> int:
    n = count_recent(
        event_filter=args.event,
        window_seconds=args.window,
        router=args.router,
    )
    print(f"[COUNT] event~={args.event}  window={args.window}s  "
          f"router={args.router or 'any'}  ->  {n}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Audit Logger (C1)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_log = sub.add_parser("log", help="Write a test event")
    p_log.add_argument("--event", required=True)
    p_log.add_argument("--incident-id", default=None)
    p_log.add_argument("--router", default=None)
    p_log.add_argument("--actor", default="cli")

    p_tail = sub.add_parser("tail", help="Show last N records")
    p_tail.add_argument("--n", type=int, default=20)

    p_count = sub.add_parser("count", help="Count matching events")
    p_count.add_argument("--event", required=True)
    p_count.add_argument("--window", type=int, default=300)
    p_count.add_argument("--router", default=None)

    args = parser.parse_args()
    if args.command == "log":
        sys.exit(cmd_log(args))
    elif args.command == "tail":
        sys.exit(cmd_tail(args))
    elif args.command == "count":
        sys.exit(cmd_count(args))


if __name__ == "__main__":
    main()
