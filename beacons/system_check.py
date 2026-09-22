#!/usr/bin/env python3
"""
Netwroxia — Phase A4b: System Readiness Check

Verifies prerequisite services + routing convergence before running
beacon/pipeline tests. Read-only. Prints READY / NOT READY.

Checks:
  1. Containerlab containers running (4)
  2. Telemetry services running (InfluxDB + Telegraf)
  3. InfluxDB HTTP reachable
  4. OSPF FULL on HO
  5. BGP Established peers on HO
  6. Beacon freshness (informational only — never blocks)

Exit codes:
  0 = READY
  1 = NOT READY (or --wait timed out)

Usage:
  python3 beacons/system_check.py           # single check
  python3 beacons/system_check.py --wait    # poll until ready (max 180s)
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import requests

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INFLUXDB_URL = "http://localhost:8086"
INFLUX_TIMEOUT = 5

CLAB_CONTAINERS = [
    "clab-netwroxia-ho-chennai",
    "clab-netwroxia-zo-bengaluru",
    "clab-netwroxia-br-koramangala",
    "clab-netwroxia-br-whitefield",
]

SERVICE_CONTAINERS = [
    "netwroxia-influxdb",
    "netwroxia-telegraf",
]

HO_CONTAINER = "clab-netwroxia-ho-chennai"
MIN_OSPF_FULL = 1
MIN_BGP_ESTABLISHED = 3

BGP_DOWN_STATES = ("Active", "Idle", "Connect", "OpenSent", "OpenConfirm")

DEFAULT_WAIT_TIMEOUT = 180
WAIT_INTERVAL = 10


# ── LOW-LEVEL HELPERS ───────────────────────────────────────────────────────
def list_running_containers():
    """Return set of running container names, or None if docker is unavailable."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        print("[FATAL] 'docker' command not found in PATH.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def vtysh(container: str, command: str):
    """Run vtysh inside container. Returns stdout string, or None on failure."""
    try:
        result = subprocess.run(
            ["docker", "exec", container, "vtysh", "-c", command],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""


# ── CHECKS ──────────────────────────────────────────────────────────────────
def check_containers() -> Tuple[bool, str]:
    running = list_running_containers()
    if running is None:
        return False, "docker ps failed"

    missing_clab = [c for c in CLAB_CONTAINERS if c not in running]
    missing_svc = [c for c in SERVICE_CONTAINERS if c not in running]

    clab_ok = len(CLAB_CONTAINERS) - len(missing_clab)
    svc_ok = len(SERVICE_CONTAINERS) - len(missing_svc)

    if missing_clab or missing_svc:
        reasons = []
        if missing_clab:
            reasons.append(f"missing clab: {', '.join(missing_clab)}")
        if missing_svc:
            reasons.append(f"missing services: {', '.join(missing_svc)}")
        return False, (
            f"clab {clab_ok}/{len(CLAB_CONTAINERS)} "
            f"services {svc_ok}/{len(SERVICE_CONTAINERS)} — "
            + "; ".join(reasons)
        )

    return True, (
        f"clab {clab_ok}/{len(CLAB_CONTAINERS)} "
        f"services {svc_ok}/{len(SERVICE_CONTAINERS)}"
    )


def check_influxdb() -> Tuple[bool, str]:
    try:
        resp = requests.get(f"{INFLUXDB_URL}/ping", timeout=INFLUX_TIMEOUT)
        if resp.status_code == 204:
            return True, "/ping returned 204"
        return False, f"/ping returned HTTP {resp.status_code}"
    except Exception as e:
        return False, f"unreachable: {type(e).__name__}"


def check_ospf() -> Tuple[bool, str]:
    out = vtysh(HO_CONTAINER, "show ip ospf neighbor")
    if out is None:
        return False, "vtysh failed on HO"
    full = sum(1 for line in out.splitlines() if "full" in line.lower())
    if full >= MIN_OSPF_FULL:
        return True, f"HO has {full} FULL neighbor(s)"
    return False, f"HO has {full} FULL neighbor(s), expected >= {MIN_OSPF_FULL}"


def check_bgp() -> Tuple[bool, str]:
    out = vtysh(HO_CONTAINER, "show ip bgp summary")
    if out is None:
        return False, "vtysh failed on HO"

    established = 0
    peer_total = 0
    for line in out.splitlines():
        if line.strip().startswith("10.255.0."):
            peer_total += 1
            if not any(state in line for state in BGP_DOWN_STATES):
                established += 1

    if established >= MIN_BGP_ESTABLISHED:
        return True, f"HO has {established}/{peer_total} Established peer(s)"
    return False, (
        f"HO has {established}/{peer_total} Established peer(s), "
        f"expected >= {MIN_BGP_ESTABLISHED}"
    )


def check_beacon_freshness() -> Tuple[bool, str]:
    """Informational — never blocks readiness."""
    state_path = PROJECT_ROOT / "beacons" / "latest_beacon_state.json"
    if not state_path.exists():
        return True, "no beacon state yet"
    try:
        with open(state_path) as f:
            data = json.load(f)
        ts_str = data.get("timestamp", "")
        if not ts_str:
            return True, "beacon state has no timestamp"
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = int((datetime.now(timezone.utc) - ts).total_seconds())
        if age < 60:
            return True, f"last beacon {age}s ago"
        return True, f"last beacon {age}s ago (stale)"
    except Exception as e:
        return True, f"could not parse beacon state: {e}"


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
CHECK_FUNCS = [
    ("Containerlab + services", check_containers),
    ("InfluxDB HTTP",           check_influxdb),
    ("OSPF on HO",              check_ospf),
    ("BGP on HO",               check_bgp),
    ("Beacon freshness",        check_beacon_freshness),
]

INFORMATIONAL = {"Beacon freshness"}


def run_all_checks() -> Tuple[bool, List[str], str]:
    """Returns (all_blocking_passed, formatted_lines, first_fail_reason)."""
    lines: List[str] = []
    all_ok = True
    first_fail = ""

    for name, func in CHECK_FUNCS:
        try:
            ok, detail = func()
        except Exception as e:
            ok, detail = False, f"exception: {type(e).__name__}: {e}"
        tag = "OK" if ok else "FAIL"
        lines.append(f"[{tag:4s}] {name:26s} {detail}")
        if not ok and name not in INFORMATIONAL:
            all_ok = False
            if not first_fail:
                first_fail = f"{name}: {detail}"

    return all_ok, lines, first_fail


def print_result(ok: bool, lines: List[str], first_fail: str) -> None:
    print("-" * 72)
    for line in lines:
        print(line)
    print("-" * 72)
    if ok:
        print("[RESULT] SYSTEM READY")
    else:
        print(f"[RESULT] NOT READY: {first_fail}")


def wait_until_ready(timeout: int) -> int:
    start = time.time()
    attempt = 0
    while True:
        attempt += 1
        elapsed = int(time.time() - start)
        print(f"[WAIT] Attempt {attempt}  (elapsed {elapsed}s / {timeout}s)")
        ok, lines, first_fail = run_all_checks()
        print_result(ok, lines, first_fail)
        if ok:
            return 0
        if elapsed >= timeout:
            print(f"[WAIT] Timed out after {elapsed}s.")
            return 1
        print(f"[WAIT] Retrying in {WAIT_INTERVAL}s...\n")
        time.sleep(WAIT_INTERVAL)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia System Readiness Check (A4b)"
    )
    parser.add_argument("--wait", action="store_true",
                        help="Poll until ready (default: single check)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_WAIT_TIMEOUT,
                        help=f"Max seconds to wait with --wait "
                             f"(default: {DEFAULT_WAIT_TIMEOUT})")
    args = parser.parse_args()

    print("=" * 72)
    print(" NETWROXIA SYSTEM READINESS CHECK (A4b)")
    print("=" * 72)
    print(f" InfluxDB : {INFLUXDB_URL}")
    mode = f"wait (max {args.timeout}s)" if args.wait else "single check"
    print(f" Mode     : {mode}")
    print()

    if args.wait:
        sys.exit(wait_until_ready(args.timeout))

    ok, lines, first_fail = run_all_checks()
    print_result(ok, lines, first_fail)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
