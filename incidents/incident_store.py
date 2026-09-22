#!/usr/bin/env python3
"""
Netwroxia — Phase C2: Incident Store (SQLite)

Persistent record of incidents and their state-transition history.
Complements the audit log (C1). Audit = attempted actions. Store =
incident lifecycle.

Schema version 1. Two tables: incidents (current state) and
transitions (append-only history of state changes).

Safe for concurrent processes: WAL journal mode, foreign keys on,
cascade deletes from incidents -> transitions.

DB path override: NETWROXIA_DB_PATH env var (useful for tests).

CLI:
    python3 incidents/incident_store.py init
    python3 incidents/incident_store.py list [--state X] [--limit N]
    python3 incidents/incident_store.py get <incident_id>
    python3 incidents/incident_store.py history <incident_id>
    python3 incidents/incident_store.py count
    python3 incidents/incident_store.py reset --yes
"""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get(
    "NETWROXIA_DB_PATH",
    str(PROJECT_ROOT / "incidents" / "incidents.db"),
))

SCHEMA_VERSION = 1

VALID_STATES = {
    "DETECTED",
    "INVESTIGATING",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "VERIFIED",
    "FAILED",
    "RESOLVED",
    "ROLLED_BACK",
    "ESCALATED",
    "CLOSED",
}

TERMINAL_STATES = {"CLOSED"}


# ── SCHEMA ──────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id   TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    state         TEXT NOT NULL,
    severity      TEXT NOT NULL DEFAULT 'UNKNOWN',
    root_router   TEXT,
    title         TEXT,
    rca_snapshot  TEXT,
    resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    reason      TEXT,
    actor       TEXT NOT NULL DEFAULT 'system',
    ts          TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state);
CREATE INDEX IF NOT EXISTS idx_incidents_updated ON incidents(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_transitions_incident ON transitions(incident_id, ts);
"""


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    """
    Yields a SQLite connection. Commits on success, rolls back on exception,
    closes always. Foreign keys are enforced per-connection.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def init_db() -> None:
    """Create schema if absent. Idempotent. Sets WAL journal mode."""
    with _connect() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            "INSERT OR REPLACE INTO _meta(key, value) VALUES(?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )


def upsert_incident(
    incident_id: str,
    severity: str = "UNKNOWN",
    root_router: Optional[str] = None,
    title: str = "",
    rca_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Insert if new (state=DETECTED). If existing, keep created_at and
    original rca_snapshot, bump updated_at. Returns the current row.

    Also writes an initial transition row on first insert.
    """
    if not incident_id or not isinstance(incident_id, str):
        raise ValueError("incident_id must be a non-empty string")

    init_db()
    now = _now_iso()
    snapshot_str = json.dumps(rca_snapshot) if rca_snapshot is not None else None

    with _connect() as conn:
        existing = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO incidents
                    (incident_id, created_at, updated_at, state, severity,
                     root_router, title, rca_snapshot, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (incident_id, now, now, "DETECTED", severity,
                 root_router, title, snapshot_str),
            )
            conn.execute(
                """
                INSERT INTO transitions
                    (incident_id, from_state, to_state, reason, actor, ts)
                VALUES (?, NULL, ?, ?, ?, ?)
                """,
                (incident_id, "DETECTED", "initial detection", "system", now),
            )
        else:
            # Keep created_at and rca_snapshot; bump updated_at, and refresh
            # severity/root/title in case the latest RCA is more specific.
            conn.execute(
                """
                UPDATE incidents
                   SET updated_at = ?,
                       severity   = ?,
                       root_router = COALESCE(?, root_router),
                       title      = CASE WHEN ? != '' THEN ? ELSE title END
                 WHERE incident_id = ?
                """,
                (now, severity, root_router, title, title, incident_id),
            )

        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()

    return _row_to_dict(row) or {}


def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_incidents(
    state: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    init_db()
    if state is not None and state not in VALID_STATES:
        raise ValueError(f"Unknown state: {state}")

    with _connect() as conn:
        if state:
            rows = conn.execute(
                """
                SELECT * FROM incidents
                 WHERE state = ?
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (state, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM incidents
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) or {} for r in rows]


def count_by_state() -> Dict[str, int]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM incidents GROUP BY state"
        ).fetchall()
    out = {s: 0 for s in VALID_STATES}
    for r in rows:
        out[r["state"]] = int(r["n"])
    return out


def add_transition(
    incident_id: str,
    to_state: str,
    from_state: Optional[str] = None,
    reason: str = "",
    actor: str = "system",
) -> Dict[str, Any]:
    """Low-level: insert a transition row. Does not touch incidents.state."""
    if to_state not in VALID_STATES:
        raise ValueError(f"Unknown to_state: {to_state}")
    if from_state is not None and from_state not in VALID_STATES:
        raise ValueError(f"Unknown from_state: {from_state}")

    init_db()
    now = _now_iso()
    with _connect() as conn:
        # Ensure parent exists (FK will error if not)
        parent = conn.execute(
            "SELECT incident_id FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if parent is None:
            raise ValueError(f"incident not found: {incident_id}")

        cur = conn.execute(
            """
            INSERT INTO transitions
                (incident_id, from_state, to_state, reason, actor, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (incident_id, from_state, to_state, reason, actor, now),
        )
        new_id = cur.lastrowid

    return {
        "id": new_id,
        "incident_id": incident_id,
        "from_state": from_state,
        "to_state": to_state,
        "reason": reason,
        "actor": actor,
        "ts": now,
    }


def get_transitions(
    incident_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM transitions
             WHERE incident_id = ?
             ORDER BY id ASC
             LIMIT ?
            """,
            (incident_id, limit),
        ).fetchall()
    return [_row_to_dict(r) or {} for r in rows]


def update_state(
    incident_id: str,
    new_state: str,
    reason: str = "",
    actor: str = "system",
) -> Dict[str, Any]:
    """
    Update incident state + write transition row atomically.

    Enforces:
      - new_state must be valid
      - incident must exist
      - incident must not be in a terminal state (CLOSED)
    Sets resolved_at when new_state is RESOLVED or CLOSED.
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"Unknown new_state: {new_state}")

    init_db()
    now = _now_iso()

    with _connect() as conn:
        row = conn.execute(
            "SELECT state FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"incident not found: {incident_id}")

        old_state = row["state"]
        if old_state in TERMINAL_STATES:
            raise ValueError(
                f"cannot transition from terminal state '{old_state}'"
            )

        resolved_at = now if new_state in {"RESOLVED", "CLOSED"} else None

        conn.execute(
            """
            UPDATE incidents
               SET state = ?,
                   updated_at = ?,
                   resolved_at = COALESCE(?, resolved_at)
             WHERE incident_id = ?
            """,
            (new_state, now, resolved_at, incident_id),
        )
        conn.execute(
            """
            INSERT INTO transitions
                (incident_id, from_state, to_state, reason, actor, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (incident_id, old_state, new_state, reason, actor, now),
        )

        updated = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()

    return _row_to_dict(updated) or {}


def delete_incident(incident_id: str) -> bool:
    """Delete incident + its transitions (cascade). Returns True if deleted."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM incidents WHERE incident_id = ?",
            (incident_id,),
        )
    return cur.rowcount > 0


def reset_all(confirm: bool = False) -> int:
    """DANGER: delete every incident and transition. Returns row count."""
    if not confirm:
        raise ValueError("reset_all requires confirm=True")
    init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM incidents")
    return cur.rowcount


# ── CLI ─────────────────────────────────────────────────────────────────────
def _print_row(r: Dict[str, Any]) -> None:
    print(f"  {r.get('incident_id', '?')[:40]:40s}  "
          f"{r.get('state', '?'):18s}  "
          f"sev={r.get('severity', '?'):8s}  "
          f"root={r.get('root_router') or '-':18s}  "
          f"updated={r.get('updated_at', '?')}")


def cmd_init(_args) -> int:
    init_db()
    print(f"[OK] Schema initialized at {DB_PATH}")
    return 0


def cmd_list(args) -> int:
    rows = list_incidents(state=args.state, limit=args.limit)
    if not rows:
        print("(no incidents)")
        return 0
    print(f"[LIST] {len(rows)} incident(s)")
    for r in rows:
        _print_row(r)
    return 0


def cmd_get(args) -> int:
    r = get_incident(args.incident_id)
    if r is None:
        print(f"[MISS] {args.incident_id}")
        return 1
    print(json.dumps(r, indent=2))
    return 0


def cmd_history(args) -> int:
    rows = get_transitions(args.incident_id)
    if not rows:
        print(f"[MISS] no transitions for {args.incident_id}")
        return 1
    print(f"[HISTORY] {len(rows)} transition(s)")
    for r in rows:
        print(f"  {r['ts']}  {r['from_state'] or '-':18s} -> "
              f"{r['to_state']:18s}  by {r['actor']:12s}  {r['reason']}")
    return 0


def cmd_count(_args) -> int:
    counts = count_by_state()
    print(f"[COUNT] total={sum(counts.values())}")
    for s in sorted(VALID_STATES):
        if counts[s] > 0:
            print(f"  {s:18s}  {counts[s]}")
    return 0


def cmd_reset(args) -> int:
    if not args.yes:
        print("[ABORT] pass --yes to confirm")
        return 1
    n = reset_all(confirm=True)
    print(f"[OK] deleted {n} incident(s)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Incident Store (C2)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize schema")

    p_list = sub.add_parser("list", help="List incidents")
    p_list.add_argument("--state", default=None)
    p_list.add_argument("--limit", type=int, default=100)

    p_get = sub.add_parser("get", help="Show one incident")
    p_get.add_argument("incident_id")

    p_hist = sub.add_parser("history", help="Show transitions")
    p_hist.add_argument("incident_id")

    sub.add_parser("count", help="Count incidents by state")

    p_reset = sub.add_parser("reset", help="Delete ALL incidents")
    p_reset.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    if args.command == "init":
        sys.exit(cmd_init(args))
    if args.command == "list":
        sys.exit(cmd_list(args))
    if args.command == "get":
        sys.exit(cmd_get(args))
    if args.command == "history":
        sys.exit(cmd_history(args))
    if args.command == "count":
        sys.exit(cmd_count(args))
    if args.command == "reset":
        sys.exit(cmd_reset(args))


if __name__ == "__main__":
    main()
