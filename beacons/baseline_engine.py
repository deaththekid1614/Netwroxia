#!/usr/bin/env python3
"""
Netwroxia — Phase A3: Baseline Engine
Computes rolling RTT baselines (mean and stddev) per router from recent
beacon data in InfluxDB. Writes beacons/latest_baselines.json for A2 and A4.

Design:
  - Reads beacon_schema.json for window/min_samples/fallback.
  - Queries InfluxDB for the last N minutes of beacon.rtt_ms.
  - Filters out MISSING beacons (rtt_ms <= 0).
  - Per router: if sample_count >= min_samples, use DB μ/σ.
                else use fallback_rtt_ms from schema, marked source=fallback.
  - Always emits an entry for every sender router in the schema.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INFLUXDB_URL = "http://localhost:8086"
DB_NAME = "netwroxia"
INFLUX_TIMEOUT = 15

SCHEMA_PATH = PROJECT_ROOT / "beacons" / "beacon_schema.json"
BASELINES_PATH = PROJECT_ROOT / "beacons" / "latest_baselines.json"


# ── LOADERS ─────────────────────────────────────────────────────────────────
def load_schema() -> Dict[str, Any]:
    if not SCHEMA_PATH.exists():
        print(f"[FATAL] Schema not found: {SCHEMA_PATH}")
        sys.exit(1)
    with open(SCHEMA_PATH, "r") as f:
        return json.load(f)


def get_past_timestamp(minutes: int) -> str:
    """Explicit UTC ISO timestamp for InfluxDB WHERE clause."""
    past = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return past.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── INFLUXDB ────────────────────────────────────────────────────────────────
def query_influx(query: str) -> Optional[Dict[str, Any]]:
    url = f"{INFLUXDB_URL}/query"
    params = {"db": DB_NAME, "q": query}
    try:
        resp = requests.get(url, params=params, timeout=INFLUX_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Influx query failed: {e}")
        return None


def fetch_baseline_stats(window_minutes: int) -> Dict[str, Dict[str, float]]:
    """
    Returns {router_name: {mean, stddev, count, min, max}}.
    Routers with zero valid samples are absent from the returned dict.
    """
    since = get_past_timestamp(window_minutes)
    query = f"""
        SELECT mean(rtt_ms) AS mean_rtt,
               stddev(rtt_ms) AS std_rtt,
               count(rtt_ms) AS n,
               min(rtt_ms) AS min_rtt,
               max(rtt_ms) AS max_rtt
        FROM beacon
        WHERE time > '{since}' AND rtt_ms > 0
        GROUP BY src_router
    """
    data = query_influx(query)
    if not data or "results" not in data or not data["results"]:
        return {}

    series_list = data["results"][0].get("series", [])
    stats: Dict[str, Dict[str, float]] = {}

    for s in series_list:
        tags = s.get("tags", {})
        router = tags.get("src_router")
        if not router:
            continue
        columns = s.get("columns", [])
        values = s.get("values", [])
        if not values:
            continue
        row = dict(zip(columns, values[0]))

        def _num(key: str) -> float:
            v = row.get(key)
            if v is None:
                return 0.0
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        stats[router] = {
            "mean": _num("mean_rtt"),
            "stddev": _num("std_rtt"),
            "count": int(_num("n")),
            "min": _num("min_rtt"),
            "max": _num("max_rtt"),
        }
    return stats


# ── COMPUTATION ─────────────────────────────────────────────────────────────
def compute_baselines(
    schema: Dict[str, Any],
    min_samples_override: Optional[int] = None,
) -> Dict[str, Any]:
    cfg = schema["baseline"]
    window_minutes = int(cfg["window_minutes"])
    min_samples = (
        int(min_samples_override)
        if min_samples_override is not None
        else int(cfg["min_samples"])
    )
    fallback_rtt = float(cfg["fallback_rtt_ms"])

    senders = [r["name"] for r in schema["routers"] if r.get("sends_beacon")]

    print(f"[INFO] Window          : {window_minutes} min")
    print(f"[INFO] Min samples     : {min_samples}")
    print(f"[INFO] Fallback RTT    : {fallback_rtt} ms")
    print(f"[INFO] Sender routers  : {', '.join(senders)}")
    print("─" * 72)

    stats = fetch_baseline_stats(window_minutes)

    routers_out: Dict[str, Dict[str, Any]] = {}

    for name in senders:
        st = stats.get(name)
        if st and st["count"] >= min_samples and st["mean"] > 0.0:
            baseline_rtt = round(st["mean"], 4)
            std_rtt = round(st["stddev"], 4)
            sample_count = int(st["count"])
            source = f"rolling_{window_minutes}min"
            print(
                f"[BASE] {name:18s} μ={baseline_rtt:8.4f}ms "
                f"σ={std_rtt:8.4f}ms n={sample_count:4d}  ({source})"
            )
        else:
            baseline_rtt = fallback_rtt
            std_rtt = 0.0
            sample_count = int(st["count"]) if st else 0
            source = "fallback" if sample_count > 0 else "no_data"
            print(
                f"[BASE] {name:18s} μ={baseline_rtt:8.4f}ms "
                f"(fallback) n={sample_count:4d}  ({source})"
            )

        routers_out[name] = {
            "baseline_rtt_ms": baseline_rtt,
            "std_rtt_ms": std_rtt,
            "sample_count": sample_count,
            "min_rtt_ms": round(st["min"], 4) if st else 0.0,
            "max_rtt_ms": round(st["max"], 4) if st else 0.0,
            "source": source,
        }

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": window_minutes,
        "min_samples": min_samples,
        "fallback_rtt_ms": fallback_rtt,
        "routers": routers_out,
    }


# ── PERSISTENCE ─────────────────────────────────────────────────────────────
def save_baselines(payload: Dict[str, Any]) -> None:
    BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINES_PATH, "w") as f:
        json.dump(payload, f, indent=2)


# ── MAIN ────────────────────────────────────────────────────────────────────
def run_once(schema: Dict[str, Any], min_samples_override: Optional[int]) -> int:
    payload = compute_baselines(schema, min_samples_override)
    save_baselines(payload)
    print("─" * 72)
    print(f"[INFO] Baselines written -> {BASELINES_PATH.relative_to(PROJECT_ROOT)}")
    return len(payload["routers"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Baseline Engine (A3)")
    parser.add_argument("--once", action="store_true",
                        help="Compute once then exit")
    parser.add_argument("--interval", type=int, default=60,
                        help="Seconds between recomputes (default: 60)")
    parser.add_argument("--min-samples", type=int, default=None,
                        help="Override schema min_samples (useful for testing)")
    args = parser.parse_args()

    schema = load_schema()

    print("=" * 72)
    print(" NETWROXIA BASELINE ENGINE (A3)")
    print("=" * 72)
    print(f" InfluxDB : {INFLUXDB_URL} / {DB_NAME}")
    print(f" Output   : {BASELINES_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Mode     : {'once' if args.once else 'continuous (every ' + str(args.interval) + 's)'}")
    print("=" * 72)

    if args.once:
        n = run_once(schema, args.min_samples)
        print(f"[INFO] Single cycle complete. Routers computed: {n}")
        return

    try:
        while True:
            run_once(schema, args.min_samples)
            print()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")


if __name__ == "__main__":
    main()
