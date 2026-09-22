#!/usr/bin/env python3
"""
Netwroxia — Phase A5: Fault Scenario Library

14 fault scenarios wrapping tc / ip / vtysh / docker commands. Each
scenario has apply() and reset(). Runnable from CLI or importable as
a library for later phases.

Link naming convention:
  <src>-<dst>  means "apply on <src> container's interface facing <dst>".
  tc netem affects EGRESS only, so this delays the direction the traffic
  leaves <src> toward <dst>.

State tracked in fault_sim/active_faults.json so reset-all can clean up.

Usage:
  python3 fault_sim/scenario_library.py list
  python3 fault_sim/scenario_library.py apply latency --link ho-zo --value 100
  python3 fault_sim/scenario_library.py apply container_stop --container zo-bengaluru
  python3 fault_sim/scenario_library.py reset latency --link ho-zo
  python3 fault_sim/scenario_library.py reset-all
  python3 fault_sim/scenario_library.py status
"""

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "fault_sim" / "active_faults.json"

# Container names (verified against topology.yml)
CONTAINERS = {
    "ho-chennai":     "clab-netwroxia-ho-chennai",
    "zo-bengaluru":   "clab-netwroxia-zo-bengaluru",
    "br-koramangala": "clab-netwroxia-br-koramangala",
    "br-whitefield":  "clab-netwroxia-br-whitefield",
}

# Verified data-plane interface map. See A5 plan for details.
LINKS: Dict[str, Tuple[str, str]] = {
    "ho-zo":    ("ho-chennai",     "eth1"),  # HO egress toward ZO
    "zo-ho":    ("zo-bengaluru",   "eth1"),  # ZO egress toward HO
    "zo-kora":  ("zo-bengaluru",   "eth2"),  # ZO egress toward BR-Koramangala
    "kora-zo":  ("br-koramangala", "eth1"),  # BR-Koramangala egress toward ZO
    "zo-white": ("zo-bengaluru",   "eth3"),  # ZO egress toward BR-Whitefield
    "white-zo": ("br-whitefield",  "eth1"),  # BR-Whitefield egress toward ZO
}

EXEC_TIMEOUT = 15
DOCKER_TIMEOUT = 30


# ── LOW-LEVEL HELPERS ───────────────────────────────────────────────────────
def container_of(name: str) -> str:
    """Resolve short name to full container name."""
    if name in CONTAINERS:
        return CONTAINERS[name]
    if name.startswith("clab-netwroxia-"):
        return name
    raise ValueError(f"Unknown container: {name}")


def run_in_container(container: str, cmd: str, timeout: int = EXEC_TIMEOUT):
    """Run a shell command inside a container. Returns (rc, stdout, stderr)."""
    full = ["docker", "exec", container, "sh", "-c", cmd]
    try:
        result = subprocess.run(
            full, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", "docker not found"
    except Exception as e:
        return 1, "", str(e)
    return result.returncode, result.stdout or "", result.stderr or ""


def run_local(cmd: list, timeout: int = DOCKER_TIMEOUT):
    """Run a command on the host (not inside a container)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:
        return 1, "", str(e)
    return result.returncode, result.stdout or "", result.stderr or ""


def parse_link(link: str) -> Tuple[str, str, str]:
    """Return (container_short, container_full, iface) for a link key."""
    if link not in LINKS:
        raise ValueError(
            f"Unknown link: {link}. Available: {', '.join(LINKS.keys())}"
        )
    short, iface = LINKS[link]
    return short, CONTAINERS[short], iface


def apply_qdisc(container: str, iface: str, qdisc_spec: str) -> Tuple[bool, str]:
    """
    Apply a root qdisc. Tries add, falls back to change if already present.
    qdisc_spec: e.g. 'netem delay 100ms' or 'tbf rate 1mbit burst 32kbit latency 400ms'.
    """
    # Clean slate — remove any existing root qdisc (ignore failure)
    run_in_container(container, f"tc qdisc del dev {iface} root 2>/dev/null")

    cmd = f"tc qdisc add dev {iface} root {qdisc_spec}"
    rc, out, err = run_in_container(container, cmd)
    if rc == 0:
        return True, f"applied: {qdisc_spec}"

    # Fallback: change
    cmd2 = f"tc qdisc change dev {iface} root {qdisc_spec}"
    rc2, out2, err2 = run_in_container(container, cmd2)
    if rc2 == 0:
        return True, f"changed: {qdisc_spec}"

    return False, f"tc failed: {err.strip() or err2.strip()}"


def del_qdisc(container: str, iface: str) -> Tuple[bool, str]:
    """Remove root qdisc. Succeeds even if none existed."""
    rc, out, err = run_in_container(
        container, f"tc qdisc del dev {iface} root 2>/dev/null; echo ok"
    )
    return True, f"cleared root qdisc on {iface}"


# ── STATE MANAGEMENT ────────────────────────────────────────────────────────
def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"active": []}
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        if "active" not in data:
            return {"active": []}
        return data
    except Exception:
        return {"active": []}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def record_fault(scenario: str, params: Dict[str, Any]) -> None:
    state = load_state()
    state["active"].append({
        "scenario": scenario,
        "params": params,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    })
    save_state(state)


def forget_fault(scenario: str, params: Dict[str, Any]) -> None:
    """Remove matching entries from active state (best-effort matching)."""
    state = load_state()
    def _matches(entry: Dict[str, Any]) -> bool:
        if entry.get("scenario") != scenario:
            return False
        for k, v in params.items():
            if entry.get("params", {}).get(k) != v:
                return False
        return True
    state["active"] = [e for e in state["active"] if not _matches(e)]
    save_state(state)


# ── SCENARIO IMPLEMENTATIONS ────────────────────────────────────────────────
# Each scenario is a pair of functions (apply, reset). Both return
# (success: bool, message: str). Signature includes a params dict.

def sc_latency_apply(link: str, value: int) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return apply_qdisc(ctr, iface, f"netem delay {value}ms")

def sc_latency_reset(link: str, value: int) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


def sc_loss_apply(link: str, value: float) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return apply_qdisc(ctr, iface, f"netem loss {value}%")

def sc_loss_reset(link: str, value: float) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


def sc_congestion_apply(link: str, value: int) -> Tuple[bool, str]:
    """value = rate in kbit. Default 1000 (1mbit)."""
    short, ctr, iface = parse_link(link)
    rate = f"{value}kbit"
    return apply_qdisc(ctr, iface, f"tbf rate {rate} burst 32kbit latency 400ms")

def sc_congestion_reset(link: str, value: int) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


def sc_jitter_apply(link: str, value: int) -> Tuple[bool, str]:
    """value = mean delay in ms. Jitter fixed at 20ms."""
    short, ctr, iface = parse_link(link)
    return apply_qdisc(ctr, iface, f"netem delay {value}ms 20ms distribution normal")

def sc_jitter_reset(link: str, value: int) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


def sc_bandwidth_flood_apply(link: str, value: int) -> Tuple[bool, str]:
    """Severe rate limit — value in kbit, default 50."""
    short, ctr, iface = parse_link(link)
    rate = f"{value}kbit"
    return apply_qdisc(ctr, iface, f"tbf rate {rate} burst 8kbit latency 400ms")

def sc_bandwidth_flood_reset(link: str, value: int) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


def sc_blackhole_apply(link: str, target: str = "") -> Tuple[bool, str]:
    """Blackhole traffic to a specific target IP."""
    if not target:
        return False, "blackhole requires --target <IP>"
    short, ctr, iface = parse_link(link)
    rc, out, err = run_in_container(
        ctr, f"ip route add blackhole {target} 2>&1 || echo exists"
    )
    return True, f"blackholed {target} on {short}"

def sc_blackhole_reset(link: str, target: str = "") -> Tuple[bool, str]:
    if not target:
        return False, "blackhole reset requires --target <IP>"
    short, ctr, iface = parse_link(link)
    run_in_container(ctr, f"ip route del blackhole {target} 2>/dev/null; echo ok")
    return True, f"removed blackhole {target} on {short}"


def sc_interface_down_apply(link: str, value: int = 0) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    rc, out, err = run_in_container(ctr, f"ip link set {iface} down")
    if rc == 0:
        return True, f"{iface} down on {short}"
    return False, f"failed: {err.strip()}"

def sc_interface_down_reset(link: str, value: int = 0) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    rc, out, err = run_in_container(ctr, f"ip link set {iface} up")
    if rc == 0:
        return True, f"{iface} up on {short}"
    return False, f"failed: {err.strip()}"


def _resolve_container(name: str) -> str:
    """Accept short name or full container name."""
    if name in CONTAINERS:
        return CONTAINERS[name]
    if name.startswith("clab-netwroxia-"):
        return name
    raise ValueError(f"Unknown container: {name}")


def sc_container_stop_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    """
    DESTRUCTIVE — docker stop on a containerlab container destroys its
    point-to-point veth links. docker start does NOT restore them.
    Recovery requires `containerlab destroy + deploy`, available via:
        python3 fault_sim/scenario_library.py recover-topology
    Prefer `container_pause` for reversible container-level faults.
    """
    if not container:
        return False, "container_stop requires --container <name>"
    full = _resolve_container(container)
    rc, out, err = run_local(["docker", "stop", full])
    if rc == 0:
        return True, (
            f"stopped {full} (DESTRUCTIVE: veth links destroyed; "
            f"run 'recover-topology' to restore)"
        )
    return False, f"failed: {err.strip()}"

def sc_container_stop_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    """
    NOTE: docker start alone does NOT restore veth links. This function
    returns failure with a clear instruction; user must run recover-topology.
    """
    return False, (
        "container_stop cannot be recovered by docker start alone. "
        "Run: python3 fault_sim/scenario_library.py recover-topology"
    )


def sc_container_pause_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "container_pause requires --container <name>"
    full = _resolve_container(container)
    rc, out, err = run_local(["docker", "pause", full])
    if rc == 0:
        return True, f"paused {full}"
    return False, f"failed: {err.strip()}"

def sc_container_pause_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "container_pause reset requires --container <name>"
    full = _resolve_container(container)
    rc, out, err = run_local(["docker", "unpause", full])
    if rc == 0:
        return True, f"unpaused {full}"
    return False, f"failed: {err.strip()}"


def sc_bgp_peer_reset_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "bgp_peer_reset requires --container <name>"
    full = _resolve_container(container)
    rc, out, err = run_in_container(
        full, 'vtysh -c "clear ip bgp *" 2>&1'
    )
    if rc == 0:
        return True, f"BGP sessions reset on {container} (re-establish ~30s)"
    return False, f"failed: {err.strip()}"

def sc_bgp_peer_reset_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    # Nothing to do — BGP re-establishes itself
    return True, "no-op (BGP auto-recovers)"


def sc_ospf_adjacency_reset_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "ospf_adjacency_reset requires --container <name>"
    full = _resolve_container(container)
    rc, out, err = run_in_container(
        full, 'vtysh -c "clear ip ospf process" 2>&1'
    )
    if rc == 0:
        return True, f"OSPF process cleared on {container} (adjacencies re-form ~30s)"
    return False, f"failed: {err.strip()}"

def sc_ospf_adjacency_reset_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    return True, "no-op (OSPF auto-recovers)"


def sc_cpu_stress_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "cpu_stress requires --container <name>"
    full = _resolve_container(container)
    # Launch 2 background CPU burners for 120s
    cmd = "(for i in 1 2; do (yes > /dev/null &) ; done) ; sleep 120 ; pkill yes 2>/dev/null ; echo done"
    # Run detached via nohup so we don't block
    rc, out, err = run_in_container(
        full, f"nohup sh -c {shlex.quote(cmd)} >/dev/null 2>&1 & echo launched"
    )
    if rc == 0:
        return True, f"cpu_stress running on {container} for 120s"
    return False, f"failed: {err.strip()}"

def sc_cpu_stress_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "cpu_stress reset requires --container <name>"
    full = _resolve_container(container)
    run_in_container(full, "pkill yes 2>/dev/null; echo ok")
    return True, f"cpu_stress stopped on {container}"


def sc_mem_stress_apply(container: str = "", value: int = 0) -> Tuple[bool, str]:
    """value = MB to allocate. Default 200."""
    if not container:
        return False, "mem_stress requires --container <name>"
    mb = value if value > 0 else 200
    full = _resolve_container(container)
    # Allocate in background, sleep 120s, then release
    py = (
        f"import time; a = bytearray({mb} * 1024 * 1024); "
        f"time.sleep(120); del a"
    )
    cmd = f"nohup python3 -c {shlex.quote(py)} >/dev/null 2>&1 & echo launched"
    rc, out, err = run_in_container(full, cmd)
    if rc == 0:
        return True, f"mem_stress {mb}MB on {container} for 120s"
    return False, f"failed: {err.strip()}"

def sc_mem_stress_reset(container: str = "", value: int = 0) -> Tuple[bool, str]:
    if not container:
        return False, "mem_stress reset requires --container <name>"
    full = _resolve_container(container)
    run_in_container(full, "pkill -f 'bytearray' 2>/dev/null; echo ok")
    return True, f"mem_stress stopped on {container}"


def sc_gradual_degradation_apply(link: str, value: int = 0) -> Tuple[bool, str]:
    """Ramp latency 10 → 100ms in 10ms increments over 60s (background)."""
    short, ctr, iface = parse_link(link)
    # Build a shell script that ramps over 60s. Runs detached.
    script = (
        f"for d in 10 20 30 40 50 60 70 80 90 100; do "
        f"tc qdisc change dev {iface} root netem delay ${{d}}ms 2>/dev/null || "
        f"tc qdisc add dev {iface} root netem delay ${{d}}ms 2>/dev/null; "
        f"sleep 6; "
        f"done; "
        f"tc qdisc del dev {iface} root 2>/dev/null; echo done"
    )
    rc, out, err = run_in_container(
        ctr, f"nohup sh -c {shlex.quote(script)} >/dev/null 2>&1 & echo launched"
    )
    if rc == 0:
        return True, f"gradual_degradation running on {link} (60s ramp, auto-clears)"
    return False, f"failed: {err.strip()}"

def sc_gradual_degradation_reset(link: str, value: int = 0) -> Tuple[bool, str]:
    short, ctr, iface = parse_link(link)
    return del_qdisc(ctr, iface)


# ── SCENARIO REGISTRY ───────────────────────────────────────────────────────
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "latency": {
        "apply": sc_latency_apply,
        "reset": sc_latency_reset,
        "requires": "link",
        "default_value": 100,
        "value_unit": "ms",
        "desc": "Add constant latency on link (tc netem delay)",
    },
    "loss": {
        "apply": sc_loss_apply,
        "reset": sc_loss_reset,
        "requires": "link",
        "default_value": 20.0,
        "value_unit": "%",
        "desc": "Add packet loss on link (tc netem loss)",
    },
    "congestion": {
        "apply": sc_congestion_apply,
        "reset": sc_congestion_reset,
        "requires": "link",
        "default_value": 1000,
        "value_unit": "kbit",
        "desc": "Rate-limit link (tc tbf, default 1mbit)",
    },
    "jitter": {
        "apply": sc_jitter_apply,
        "reset": sc_jitter_reset,
        "requires": "link",
        "default_value": 50,
        "value_unit": "ms mean, ±20ms jitter",
        "desc": "Add latency jitter on link (tc netem delay with jitter)",
    },
    "bandwidth_flood": {
        "apply": sc_bandwidth_flood_apply,
        "reset": sc_bandwidth_flood_reset,
        "requires": "link",
        "default_value": 50,
        "value_unit": "kbit",
        "desc": "Severe rate limit — near-DoS (tc tbf 50kbit)",
    },
    "blackhole": {
        "apply": sc_blackhole_apply,
        "reset": sc_blackhole_reset,
        "requires": "link+target",
        "default_value": 0,
        "value_unit": "n/a",
        "desc": "Blackhole traffic to specific target IP",
    },
    "interface_down": {
        "apply": sc_interface_down_apply,
        "reset": sc_interface_down_reset,
        "requires": "link",
        "default_value": 0,
        "value_unit": "n/a",
        "desc": "Bring interface down (ip link set down)",
    },
    "container_stop": {
        "apply": sc_container_stop_apply,
        "reset": sc_container_stop_reset,
        "requires": "container",
        "default_value": 0,
        "value_unit": "n/a (DESTRUCTIVE — see reset)",
        "desc": "Stop container (DESTRUCTIVE: destroys veth links; recover via 'recover-topology')",
    },
    "container_pause": {
        "apply": sc_container_pause_apply,
        "reset": sc_container_pause_reset,
        "requires": "container",
        "default_value": 0,
        "value_unit": "n/a",
        "desc": "Pause container (docker pause)",
    },
    "bgp_peer_reset": {
        "apply": sc_bgp_peer_reset_apply,
        "reset": sc_bgp_peer_reset_reset,
        "requires": "container",
        "default_value": 0,
        "value_unit": "n/a",
        "desc": "Reset BGP sessions (vtysh clear ip bgp)",
    },
    "ospf_adjacency_reset": {
        "apply": sc_ospf_adjacency_reset_apply,
        "reset": sc_ospf_adjacency_reset_reset,
        "requires": "container",
        "default_value": 0,
        "value_unit": "n/a",
        "desc": "Restart OSPF process (vtysh clear ip ospf)",
    },
    "cpu_stress": {
        "apply": sc_cpu_stress_apply,
        "reset": sc_cpu_stress_reset,
        "requires": "container",
        "default_value": 0,
        "value_unit": "n/a (2 burners, 120s)",
        "desc": "CPU stress inside container",
    },
    "mem_stress": {
        "apply": sc_mem_stress_apply,
        "reset": sc_mem_stress_reset,
        "requires": "container",
        "default_value": 200,
        "value_unit": "MB (120s)",
        "desc": "Memory pressure inside container",
    },
    "gradual_degradation": {
        "apply": sc_gradual_degradation_apply,
        "reset": sc_gradual_degradation_reset,
        "requires": "link",
        "default_value": 0,
        "value_unit": "n/a (60s ramp)",
        "desc": "Ramp latency 10→100ms over 60s (auto-clears)",
    },
}


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def apply_scenario(
    scenario: str,
    link: Optional[str] = None,
    container: Optional[str] = None,
    value: Optional[float] = None,
    target: Optional[str] = None,
) -> Tuple[bool, str]:
    if scenario not in SCENARIOS:
        return False, f"Unknown scenario: {scenario}"
    spec = SCENARIOS[scenario]
    params: Dict[str, Any] = {}

    if link:
        params["link"] = link
    if container:
        params["container"] = container
    if value is not None:
        params["value"] = value
    if target:
        params["target"] = target

    try:
        if scenario in ("blackhole",):
            ok, msg = spec["apply"](link=link, target=target or "")
        elif spec["requires"] == "link":
            v = value if value is not None else spec["default_value"]
            ok, msg = spec["apply"](link=link, value=v)
        elif spec["requires"] == "container":
            v = value if value is not None else spec["default_value"]
            ok, msg = spec["apply"](container=container, value=v)
        else:
            return False, f"Internal: unhandled requires '{spec['requires']}'"
    except ValueError as e:
        return False, str(e)

    if ok:
        record_fault(scenario, params)
    return ok, msg


def reset_scenario(
    scenario: str,
    link: Optional[str] = None,
    container: Optional[str] = None,
    value: Optional[float] = None,
    target: Optional[str] = None,
) -> Tuple[bool, str]:
    if scenario not in SCENARIOS:
        return False, f"Unknown scenario: {scenario}"
    spec = SCENARIOS[scenario]
    params: Dict[str, Any] = {}

    if link:
        params["link"] = link
    if container:
        params["container"] = container
    if value is not None:
        params["value"] = value
    if target:
        params["target"] = target

    try:
        if scenario in ("blackhole",):
            ok, msg = spec["reset"](link=link, target=target or "")
        elif spec["requires"] == "link":
            v = value if value is not None else spec["default_value"]
            ok, msg = spec["reset"](link=link, value=v)
        elif spec["requires"] == "container":
            v = value if value is not None else spec["default_value"]
            ok, msg = spec["reset"](container=container, value=v)
        else:
            return False, f"Internal: unhandled requires '{spec['requires']}'"
    except ValueError as e:
        return False, str(e)

    if ok:
        forget_fault(scenario, params)
    return ok, msg


def reset_all_scenarios() -> int:
    """Reset every fault recorded in active_faults.json."""
    state = load_state()
    active = list(state.get("active", []))
    if not active:
        print("[INFO] No active faults to reset.")
        return 0

    print(f"[INFO] Resetting {len(active)} active fault(s)...")
    success = 0
    for entry in active:
        scenario = entry.get("scenario")
        params = entry.get("params", {})
        ok, msg = reset_scenario(
            scenario=scenario,
            link=params.get("link"),
            container=params.get("container"),
            value=params.get("value"),
            target=params.get("target"),
        )
        tag = "OK" if ok else "FAIL"
        print(f"[{tag:4s}] reset {scenario:24s} {msg}")
        if ok:
            success += 1

    # Clear the file
    save_state({"active": []})
    print(f"[INFO] {success}/{len(active)} faults reset.")
    return success


def recover_topology() -> int:
    """Destroy + redeploy the containerlab topology. Required after any
    destructive fault (e.g. container_stop). Prompts for sudo password."""
    topo = PROJECT_ROOT / "network" / "containerlab" / "topology.yml"
    if not topo.exists():
        print(f"[FATAL] topology.yml not found at {topo}")
        return 1

    print("[INFO] Destroying containerlab topology...")
    rc, out, err = run_local(
        ["sudo", "containerlab", "destroy", "-t", str(topo)],
        timeout=60,
    )
    if rc != 0:
        print(f"[FAIL] destroy failed: {err.strip() or out.strip()}")
        return 1
    print("[OK]   topology destroyed")

    print("[INFO] Deploying fresh topology...")
    rc, out, err = run_local(
        ["sudo", "containerlab", "deploy", "-t", str(topo)],
        timeout=180,
    )
    if rc != 0:
        print(f"[FAIL] deploy failed: {err.strip() or out.strip()}")
        return 1
    print("[OK]   topology deployed")

    save_state({"active": []})
    print("[OK]   fault state cleared")

    print()
    print("[IMPORTANT] Wait 90-120 seconds for OSPF+BGP convergence.")
    print("[SUGGEST]   sleep 120 && python3 beacons/system_check.py")
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_list() -> None:
    print("=" * 78)
    print(" NETWROXIA FAULT SCENARIOS")
    print("=" * 78)
    for name, spec in SCENARIOS.items():
        print(f"  {name:24s} requires={spec['requires']:16s}")
        print(f"  {'':24s} {spec['desc']}")
        print()
    print("Available links:")
    for k, (short, iface) in LINKS.items():
        print(f"  {k:12s} -> {short:16s} {iface}")
    print()
    print("Available containers:")
    for k in CONTAINERS:
        print(f"  {k}")


def cmd_status() -> None:
    state = load_state()
    active = state.get("active", [])
    print("=" * 78)
    print(" ACTIVE FAULTS")
    print("=" * 78)
    if not active:
        print("  (none)")
        return
    for entry in active:
        s = entry.get("scenario", "?")
        p = entry.get("params", {})
        t = entry.get("applied_at", "?")
        print(f"  {s:24s} {p}  @ {t}")


def cmd_apply(args) -> int:
    ok, msg = apply_scenario(
        scenario=args.scenario,
        link=args.link,
        container=args.container,
        value=args.value,
        target=args.target,
    )
    tag = "OK" if ok else "FAIL"
    print(f"[{tag}] {args.scenario}: {msg}")
    return 0 if ok else 1


def cmd_reset(args) -> int:
    ok, msg = reset_scenario(
        scenario=args.scenario,
        link=args.link,
        container=args.container,
        value=args.value,
        target=args.target,
    )
    tag = "OK" if ok else "FAIL"
    print(f"[{tag}] {args.scenario}: {msg}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Fault Scenario Library (A5)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all scenarios")
    sub.add_parser("status", help="Show active faults")
    sub.add_parser("reset-all", help="Reset every recorded fault")
    sub.add_parser("recover-topology",
                   help="Destroy + redeploy topology (required after container_stop)")

    p_apply = sub.add_parser("apply", help="Apply a fault")
    p_apply.add_argument("scenario", choices=list(SCENARIOS.keys()))
    p_apply.add_argument("--link", choices=list(LINKS.keys()), default=None)
    p_apply.add_argument("--container", choices=list(CONTAINERS.keys()), default=None)
    p_apply.add_argument("--value", type=float, default=None)
    p_apply.add_argument("--target", type=str, default=None)

    p_reset = sub.add_parser("reset", help="Reset a fault")
    p_reset.add_argument("scenario", choices=list(SCENARIOS.keys()))
    p_reset.add_argument("--link", choices=list(LINKS.keys()), default=None)
    p_reset.add_argument("--container", choices=list(CONTAINERS.keys()), default=None)
    p_reset.add_argument("--value", type=float, default=None)
    p_reset.add_argument("--target", type=str, default=None)

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
        sys.exit(0)
    elif args.command == "status":
        cmd_status()
        sys.exit(0)
    elif args.command == "reset-all":
        reset_all_scenarios()
        sys.exit(0)
    elif args.command == "apply":
        sys.exit(cmd_apply(args))
    elif args.command == "recover-topology":
        sys.exit(recover_topology())
    elif args.command == "reset":
        sys.exit(cmd_reset(args))


if __name__ == "__main__":
    main()
