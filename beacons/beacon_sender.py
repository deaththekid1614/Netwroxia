#!/usr/bin/env python3
"""
Netwroxia — Phase A2: Beacon Sender
Host-side agent. Probes each non-HO router with ICMP packets, parses
RTT/loss, computes status, writes 'beacon' measurement to InfluxDB 1.8.

Design notes:
  - Runs on HOST (not inside containers), matching inject_faults.py style.
  - Uses `docker exec` to run `ping` FROM each sender router TO HO-Chennai
    loopback (10.255.0.1). This exercises the real data-plane path.
  - Reads beacons/beacon_schema.json for router list + thresholds.
  - Reads beacons/latest_baselines.json (written by A3) if present.
  - Writes beacons/latest_beacon_state.json for A4 to consume.
  - Tracks monotonic seq per router in beacons/.beacon_seq.json.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INFLUXDB_URL = "http://localhost:8086"
DB_NAME = "netwroxia"
RETENTION_POLICY = "one_week"
INFLUX_TIMEOUT = 10

SCHEMA_PATH = PROJECT_ROOT / "beacons" / "beacon_schema.json"
BASELINES_PATH = PROJECT_ROOT / "beacons" / "latest_baselines.json"
STATE_PATH = PROJECT_ROOT / "beacons" / "latest_beacon_state.json"
SEQ_PATH = PROJECT_ROOT / "beacons" / ".beacon_seq.json"

DEFAULT_INTERVAL = 10
DEFAULT_PING_COUNT = 3
PING_PER_PACKET_TIMEOUT = 2
PING_OVERALL_TIMEOUT = 5
EXEC_TIMEOUT_SECONDS = 15

STATUS_NAMES = {0: "OK", 1: "DEGRADED", 2: "CRITICAL", 3: "MISSING"}


# ── SCHEMA / STATE LOADERS ──────────────────────────────────────────────────
def load_schema() -> Dict[str, Any]:
    if not SCHEMA_PATH.exists():
        print(f"[FATAL] Schema not found: {SCHEMA_PATH}")
        sys.exit(1)
    with open(SCHEMA_PATH, "r") as f:
        return json.load(f)


def load_seq() -> Dict[str, int]:
    if SEQ_PATH.exists():
        try:
            with open(SEQ_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seq(seq_map: Dict[str, int]) -> None:
    SEQ_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEQ_PATH, "w") as f:
        json.dump(seq_map, f, indent=2)


def load_baselines() -> Dict[str, float]:
    """Return {router_name: baseline_rtt_ms}. Empty dict if no file yet."""
    if not BASELINES_PATH.exists():
        return {}
    try:
        with open(BASELINES_PATH, "r") as f:
            data = json.load(f)
        routers = data.get("routers", {})
        return {name: float(entry.get("baseline_rtt_ms", 0.0))
                for name, entry in routers.items()}
    except Exception as e:
        print(f"[WARN] Could not read baselines: {e}")
        return {}


# ── PING EXECUTION ──────────────────────────────────────────────────────────
def run_ping(container: str, target: str, count: int) -> str:
    """Run ping inside container. Returns stdout (may be empty on failure)."""
    cmd = [
        "docker", "exec", container,
        "ping", "-c", str(count),
        "-W", str(PING_PER_PACKET_TIMEOUT),
        "-w", str(PING_OVERALL_TIMEOUT),
        target,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=EXEC_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        print(f"[WARN] docker exec timed out for {container}")
        return ""
    except FileNotFoundError:
        print("[FATAL] 'docker' command not found in PATH.")
        sys.exit(1)
    except Exception as e:
        print(f"[WARN] docker exec failed for {container}: {e}")
        return ""

    stderr = (result.stderr or "").strip().lower()
    if "no such container" in stderr:
        print(f"[ERROR] Container '{container}' is not running.")
        return ""
    if "executable file not found" in stderr or "not found" in stderr:
        print(f"[ERROR] 'ping' binary missing inside {container}.")
        return ""
    return result.stdout or ""


def parse_ping_output(raw: str, expected_count: int) -> Dict[str, float]:
    """
    Parse ping output. Handles iputils ('rtt min/avg/max/mdev') and
    busybox ('round-trip min/avg/max').

    Returns: rtt_ms, rtt_min_ms, rtt_max_ms, loss_pct,
             packets_sent, packets_recv
    """
    out = {
        "rtt_ms": 0.0,
        "rtt_min_ms": 0.0,
        "rtt_max_ms": 0.0,
        "loss_pct": 100.0,
        "packets_sent": expected_count,
        "packets_recv": 0,
    }
    if not raw:
        return out

    m = re.search(r"([\d.]+)%\s*packet loss", raw)
    if m:
        out["loss_pct"] = float(m.group(1))

    m = re.search(
        r"(\d+)\s+packets?\s+transmitted,\s+(\d+)\s+(?:packets\s+)?received",
        raw,
    )
    if m:
        out["packets_sent"] = int(m.group(1))
        out["packets_recv"] = int(m.group(2))

    m = re.search(
        r"(?:rtt|round-trip)\s+min/avg/max(?:/mdev)?\s*=\s*"
        r"([\d.]+)/([\d.]+)/([\d.]+)",
        raw,
    )
    if m:
        out["rtt_min_ms"] = float(m.group(1))
        out["rtt_ms"] = float(m.group(2))
        out["rtt_max_ms"] = float(m.group(3))

    return out


# ── STATUS COMPUTATION ──────────────────────────────────────────────────────
def compute_status(metrics: Dict[str, float], baseline_rtt: float) -> int:
    """
    0 = OK, 1 = DEGRADED, 2 = CRITICAL, 3 = MISSING.
    Thresholds from beacon_schema.json.
    """
    loss = metrics["loss_pct"]
    rtt = metrics["rtt_ms"]

    if loss >= 100.0 or metrics["packets_recv"] == 0:
        return 3

    if baseline_rtt > 0.0 and rtt > 0.0:
        ratio = rtt / baseline_rtt
        if loss <= 1.0 and ratio <= 1.5:
            return 0
        if loss <= 10.0 and ratio <= 3.0:
            return 1
        return 2

    # No baseline yet — absolute fallback
    if loss <= 1.0 and rtt <= 30.0:
        return 0
    if loss <= 10.0 and rtt <= 100.0:
        return 1
    return 2


# ── INFLUXDB WRITE ──────────────────────────────────────────────────────────
def write_beacon(
    src_router: str,
    dst_router: str,
    path_label: str,
    seq: int,
    metrics: Dict[str, float],
    status_code: int,
    baseline_rtt_ms: float,
    deviation_pct: float,
) -> bool:
    """Write one beacon point via InfluxDB line protocol."""
    url = f"{INFLUXDB_URL}/write"
    params = {"db": DB_NAME, "rp": RETENTION_POLICY, "precision": "ns"}

    tags = (
        f"src_router={src_router},"
        f"dst_router={dst_router},"
        f"path={path_label}"
    )
    fields = (
        f"seq={seq}i,"
        f"rtt_ms={metrics['rtt_ms']},"
        f"rtt_min_ms={metrics['rtt_min_ms']},"
        f"rtt_max_ms={metrics['rtt_max_ms']},"
        f"loss_pct={metrics['loss_pct']},"
        f"status_code={status_code}i,"
        f"baseline_rtt_ms={baseline_rtt_ms},"
        f"deviation_pct={deviation_pct}"
    )
    ts_ns = time.time_ns()
    line = f"beacon,{tags} {fields} {ts_ns}\n"

    try:
        resp = requests.post(
            url, params=params,
            data=line.encode("utf-8"),
            timeout=INFLUX_TIMEOUT,
        )
        if resp.status_code in (200, 204):
            return True
        print(f"[ERROR] Influx write HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[ERROR] Influx write exception: {e}")
        return False


# ── PROBE ONE ROUTER ────────────────────────────────────────────────────────
def probe_router(
    router_cfg: Dict[str, Any],
    dst_name: str,
    dst_loopback: str,
    seq_map: Dict[str, int],
    baselines: Dict[str, float],
    count: int,
) -> Optional[Dict[str, Any]]:
    name = router_cfg["name"]
    container = router_cfg["container"]

    seq_map[name] = seq_map.get(name, 0) + 1
    seq = seq_map[name]

    raw = run_ping(container, dst_loopback, count)
    metrics = parse_ping_output(raw, count)

    baseline = baselines.get(name, 0.0)
    if baseline > 0.0 and metrics["rtt_ms"] > 0.0:
        deviation_pct = ((metrics["rtt_ms"] - baseline) / baseline) * 100.0
    else:
        deviation_pct = 0.0

    status_code = compute_status(metrics, baseline)
    status_str = STATUS_NAMES[status_code]
    path_label = f"{name}->{dst_name}"

    ok = write_beacon(
        src_router=name,
        dst_router=dst_name,
        path_label=path_label,
        seq=seq,
        metrics=metrics,
        status_code=status_code,
        baseline_rtt_ms=baseline,
        deviation_pct=deviation_pct,
    )

    if ok:
        print(
            f"[BEACON] {name:16s} seq={seq:4d} "
            f"rtt={metrics['rtt_ms']:7.2f}ms "
            f"loss={metrics['loss_pct']:5.1f}% "
            f"base={baseline:6.2f}ms "
            f"dev={deviation_pct:7.1f}% "
            f"-> {status_str}"
        )
    else:
        print(f"[BEACON] {name:16s} seq={seq:4d} WRITE FAILED")

    return {
        "name": name,
        "seq": seq,
        "rtt_ms": metrics["rtt_ms"],
        "rtt_min_ms": metrics["rtt_min_ms"],
        "rtt_max_ms": metrics["rtt_max_ms"],
        "loss_pct": metrics["loss_pct"],
        "packets_sent": metrics["packets_sent"],
        "packets_recv": metrics["packets_recv"],
        "status_code": status_code,
        "status": status_str,
        "baseline_rtt_ms": baseline,
        "deviation_pct": deviation_pct,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── STATE PERSISTENCE ───────────────────────────────────────────────────────
def save_state(results: List[Dict[str, Any]]) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "routers": {r["name"]: r for r in results},
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(payload, f, indent=2)


# ── ONE CYCLE ───────────────────────────────────────────────────────────────
def run_once(schema: Dict[str, Any], count: int) -> int:
    dst_name: Optional[str] = None
    dst_loopback: Optional[str] = None
    senders: List[Dict[str, Any]] = []

    for r in schema["routers"]:
        if r.get("receives_beacon") and not r.get("sends_beacon"):
            dst_name = r["name"]
            dst_loopback = r["loopback"]
        if r.get("sends_beacon"):
            senders.append(r)

    if not dst_loopback or not dst_name:
        print("[FATAL] No beacon receiver found in schema.")
        sys.exit(1)

    seq_map = load_seq()
    baselines = load_baselines()

    results: List[Dict[str, Any]] = []
    for r in senders:
        res = probe_router(r, dst_name, dst_loopback, seq_map, baselines, count)
        if res:
            results.append(res)

    save_seq(seq_map)
    save_state(results)
    return len(results)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Beacon Sender (A2)")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle then exit")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Seconds between cycles (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--count", type=int, default=DEFAULT_PING_COUNT,
                        help=f"ICMP probes per router per cycle (default: {DEFAULT_PING_COUNT})")
    args = parser.parse_args()

    schema = load_schema()

    print("=" * 72)
    print(" NETWROXIA BEACON SENDER (A2)")
    print("=" * 72)
    print(f" InfluxDB   : {INFLUXDB_URL} / {DB_NAME} (rp={RETENTION_POLICY})")
    print(f" Interval   : {args.interval}s")
    print(f" Ping count : {args.count} packets per router per cycle")
    print(f" Mode       : {'once' if args.once else 'continuous'}")
    print("=" * 72)

    if args.once:
        n = run_once(schema, args.count)
        print(f"[INFO] Single cycle complete. Beacons written: {n}")
        print(f"[INFO] State -> {STATE_PATH.relative_to(PROJECT_ROOT)}")
        print(f"[INFO] Seq   -> {SEQ_PATH.relative_to(PROJECT_ROOT)}")
        return

    try:
        while True:
            run_once(schema, args.count)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")


if __name__ == "__main__":
    main()
