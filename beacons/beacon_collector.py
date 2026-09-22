#!/usr/bin/env python3
"""
Netwroxia — Phase A4: Beacon Collector
Enriches A2's raw beacon state with consecutive-miss detection,
z-scores, and a network-wide beacon health score. Writes
beacons/latest_beacon_health.json — the canonical input for dashboard,
copilot, and RCA.

Design:
  - Pure read + compute + write. No InfluxDB access.
  - Reads A2's raw state file + A3's baselines file.
  - Tracks consecutive MISSING cycles per router in
    beacons/.beacon_miss_counter.json (persistent across runs).
  - MISSING status only declared after N consecutive cycles with
    packets_received == 0. Suppresses one-off blips.
  - Idempotent: skips a router whose seq was already processed.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_PATH = PROJECT_ROOT / "beacons" / "beacon_schema.json"
RAW_STATE_PATH = PROJECT_ROOT / "beacons" / "latest_beacon_state.json"
BASELINES_PATH = PROJECT_ROOT / "beacons" / "latest_baselines.json"
HEALTH_PATH = PROJECT_ROOT / "beacons" / "latest_beacon_health.json"
MISS_COUNTER_PATH = PROJECT_ROOT / "beacons" / ".beacon_miss_counter.json"

MISS_THRESHOLD = 3  # consecutive MISSING cycles before declaring MISSING

STATUS_NAMES = {0: "OK", 1: "DEGRADED", 2: "CRITICAL", 3: "MISSING"}
STATUS_SCORES = {0: 100, 1: 70, 2: 30, 3: 0}


# ── LOADERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Required file not found: {path}")
            print(f"[HINT]  Run the upstream stages first.")
            sys.exit(1)
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[FATAL] Malformed JSON in {path}: {e}")
        sys.exit(1)


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


# ── MISS COUNTER ────────────────────────────────────────────────────────────
def load_miss_counter() -> Dict[str, Any]:
    """Returns {"routers": {name: {"consecutive": int, "last_seq": int}}}."""
    if not MISS_COUNTER_PATH.exists():
        return {"routers": {}}
    try:
        with open(MISS_COUNTER_PATH, "r") as f:
            data = json.load(f)
        if "routers" not in data:
            return {"routers": {}}
        return data
    except Exception:
        return {"routers": {}}


def save_miss_counter(data: Dict[str, Any]) -> None:
    MISS_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MISS_COUNTER_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ── CORE LOGIC ──────────────────────────────────────────────────────────────
def process_router(
    name: str,
    raw: Dict[str, Any],
    baseline_entry: Optional[Dict[str, Any]],
    counter_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compute enriched entry for one router. Mutates counter_state in place
    (updates consecutive miss count and last_seq) — caller persists it.
    """
    seq = int(raw.get("seq", 0))
    raw_status_code = int(raw.get("status_code", 3))
    packets_recv = int(raw.get("packets_recv", 0))
    rtt_ms = float(raw.get("rtt_ms", 0.0))
    loss_pct = float(raw.get("loss_pct", 100.0))

    # Idempotency: if we already processed this seq, return the previous
    # consecutive count without incrementing again.
    prev = counter_state["routers"].get(name, {"consecutive": 0, "last_seq": -1})
    if seq == prev.get("last_seq", -1):
        consecutive = int(prev.get("consecutive", 0))
    else:
        if raw_status_code == 3 or packets_recv == 0:
            consecutive = int(prev.get("consecutive", 0)) + 1
        else:
            consecutive = 0

    counter_state["routers"][name] = {
        "consecutive": consecutive,
        "last_seq": seq,
    }

    # Final status: MISSING requires MISS_THRESHOLD consecutive cycles
    if consecutive >= MISS_THRESHOLD:
        final_code = 3
    elif raw_status_code >= 2:
        final_code = 2
    elif raw_status_code == 1:
        final_code = 1
    else:
        final_code = 0

    # Z-score from rolling baselines if available
    z_score = 0.0
    baseline_rtt = 0.0
    std_rtt = 0.0
    baseline_source = "unavailable"
    if baseline_entry:
        baseline_rtt = float(baseline_entry.get("baseline_rtt_ms", 0.0))
        std_rtt = float(baseline_entry.get("std_rtt_ms", 0.0))
        baseline_source = str(baseline_entry.get("source", "unknown"))
        if std_rtt > 0.0 and baseline_rtt > 0.0 and rtt_ms > 0.0:
            z_score = (rtt_ms - baseline_rtt) / std_rtt

    return {
        "name": name,
        "status": STATUS_NAMES[final_code],
        "status_code": final_code,
        "raw_status_code": raw_status_code,
        "score": STATUS_SCORES[final_code],
        "consecutive_misses": consecutive,
        "seq": seq,
        "rtt_ms": round(rtt_ms, 4),
        "rtt_min_ms": round(float(raw.get("rtt_min_ms", 0.0)), 4),
        "rtt_max_ms": round(float(raw.get("rtt_max_ms", 0.0)), 4),
        "loss_pct": round(loss_pct, 2),
        "baseline_rtt_ms": round(baseline_rtt, 4),
        "std_rtt_ms": round(std_rtt, 4),
        "baseline_source": baseline_source,
        "deviation_pct": round(float(raw.get("deviation_pct", 0.0)), 2),
        "z_score": round(z_score, 3),
        "last_seen": raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }


def build_health(
    raw_state: Dict[str, Any],
    baselines: Dict[str, Any],
    counter_state: Dict[str, Any],
) -> Dict[str, Any]:
    raw_routers = raw_state.get("routers", {})
    baseline_routers = baselines.get("routers", {}) if baselines else {}

    if not raw_routers:
        print("[WARN] A2 raw state contains no routers. Nothing to process.")
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "network_summary": {
                "total_routers": 0,
                "ok": 0, "degraded": 0, "critical": 0, "missing": 0,
                "beacon_health_score": 0,
            },
            "routers": {},
        }

    enriched: Dict[str, Dict[str, Any]] = {}
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    scores = []

    for name, raw in raw_routers.items():
        entry = process_router(
            name=name,
            raw=raw,
            baseline_entry=baseline_routers.get(name),
            counter_state=counter_state,
        )
        enriched[name] = entry
        counts[entry["status_code"]] += 1
        scores.append(entry["score"])

    network_score = int(round(sum(scores) / len(scores))) if scores else 0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_timestamp": raw_state.get("timestamp", ""),
        "network_summary": {
            "total_routers": len(enriched),
            "ok": counts[0],
            "degraded": counts[1],
            "critical": counts[2],
            "missing": counts[3],
            "beacon_health_score": network_score,
        },
        "routers": enriched,
    }


# ── MAIN ────────────────────────────────────────────────────────────────────
def run_once() -> Dict[str, Any]:
    raw_state = load_json(RAW_STATE_PATH, required=True)
    baselines = load_json(BASELINES_PATH, required=False)
    counter_state = load_miss_counter()

    health = build_health(raw_state, baselines or {}, counter_state)

    save_json(HEALTH_PATH, health)
    save_miss_counter(counter_state)

    return health


def print_summary(health: Dict[str, Any]) -> None:
    summary = health["network_summary"]
    print("─" * 72)
    print(f" Network score : {summary['beacon_health_score']:3d} / 100")
    print(f" Total routers : {summary['total_routers']}")
    print(f"   OK          : {summary['ok']}")
    print(f"   DEGRADED    : {summary['degraded']}")
    print(f"   CRITICAL    : {summary['critical']}")
    print(f"   MISSING     : {summary['missing']}")
    print("─" * 72)

    for name, entry in health["routers"].items():
        z = f"{entry['z_score']:+.2f}" if entry["z_score"] != 0.0 else "  n/a"
        print(
            f" {name:18s} {entry['status']:9s} "
            f"rtt={entry['rtt_ms']:7.3f}ms "
            f"base={entry['baseline_rtt_ms']:7.3f}ms "
            f"dev={entry['deviation_pct']:+7.1f}% "
            f"z={z:>6s} "
            f"misses={entry['consecutive_misses']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Beacon Collector (A4)")
    parser.add_argument("--once", action="store_true",
                        help="Process once then exit (default behavior)")
    args = parser.parse_args()

    print("=" * 72)
    print(" NETWROXIA BEACON COLLECTOR (A4)")
    print("=" * 72)
    print(f" Raw state   : {RAW_STATE_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Baselines   : {BASELINES_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output      : {HEALTH_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Counter     : {MISS_COUNTER_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 72)

    health = run_once()
    print_summary(health)
    print(f"[INFO] Health written -> {HEALTH_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
