#!/usr/bin/env python3
"""
Netwroxia — Phase B3: Banking Service Impact Analyzer

Given current network health (B1) and the service map (B2), computes:
  - Which routers are affected (direct + propagation)
  - Which banking services are down / degraded
  - Affected user count and revenue-per-minute
  - Overall severity
  - Propagation chains (root -> downstream) for RCA

Output: impact/latest_impact.json

Design rules:
  - Directly affected: B1 status in {CRITICAL, DOWN}. WARNING is not affected.
  - Propagated: upstream router affected -> downstream router affected.
  - Service DOWN if all host routers are affected.
  - Service DEGRADED if some host routers are affected.
  - Service UP if no host routers are affected.
  - Severity escalates on HO failure, RBI-critical services down, or count.

CLI:
    python3 impact/impact_analyzer.py
    python3 impact/impact_analyzer.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

HEALTH_PATH = PROJECT_ROOT / "analytics" / "latest_health.json"
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
OUTPUT_PATH = PROJECT_ROOT / "impact" / "latest_impact.json"

# Statuses that count as affected
AFFECTED_STATUSES = {"CRITICAL", "DOWN"}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Missing required file: {path}")
            print(f"[HINT]  Run upstream stages first.")
            sys.exit(1)
        print(f"[WARN] Missing optional file: {path}")
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


def build_upstream_map(propagation: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """
    Invert propagation map.
    propagation = {"A": ["B"], "B": ["C"]}  (A upstream of B, B upstream of C)
    upstream   = {"B": ["A"], "C": ["B"], "A": []}
    """
    upstream: Dict[str, List[str]] = {}
    for src, dests in propagation.items():
        upstream.setdefault(src, [])
        for dst in dests:
            upstream.setdefault(dst, []).append(src)
    return upstream


# ── AFFECTED ROUTER COMPUTATION ─────────────────────────────────────────────
def find_propagation_chain(
    router: str,
    direct_affected: Set[str],
    upstream_map: Dict[str, List[str]],
) -> List[str]:
    """
    Return a chain of affected upstream routers leading to `router`,
    or empty list if none. BFS breadth-first to find shortest chain.
    Chain is ordered root-first: [root, ..., immediate_upstream].
    """
    if router in direct_affected:
        return [router]

    visited: Set[str] = set()
    queue: List[tuple] = [(router, [])]

    while queue:
        current, path = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        for up in upstream_map.get(current, []):
            new_path = [up] + path
            if up in direct_affected:
                return new_path
            queue.append((up, new_path))

    return []


def compute_affected_routers(
    health: Dict[str, Any],
    service_map: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """
    Returns {router_name: {...}} for every router affected directly or
    via propagation. Empty dict if network is fully healthy.
    """
    routers_cfg = service_map["routers"]
    propagation = service_map.get("propagation", {})
    upstream_map = build_upstream_map(
        {k: v for k, v in propagation.items() if k != "description"}
    )

    # Directly affected
    direct: Set[str] = set()
    for r, entry in health["routers"].items():
        if entry.get("status") in AFFECTED_STATUSES:
            direct.add(r)

    affected: Dict[str, Dict[str, Any]] = {}

    for router_name, cfg in routers_cfg.items():
        entry = health["routers"].get(router_name, {})
        if router_name in direct:
            affected[router_name] = {
                "directly_affected": True,
                "via_propagation_from": None,
                "propagation_chain": [router_name],
                "score": entry.get("score", 0.0),
                "status": entry.get("status", "UNKNOWN"),
                "role": cfg.get("role", "unknown"),
                "location": cfg.get("location", "unknown"),
                "user_count": int(cfg.get("user_count", 0)),
                "revenue_per_min_inr": int(cfg.get("revenue_per_min_inr", 0)),
            }
        else:
            chain = find_propagation_chain(router_name, direct, upstream_map)
            if chain:
                affected[router_name] = {
                    "directly_affected": False,
                    "via_propagation_from": chain[-2] if len(chain) >= 2 else None,
                    "propagation_chain": chain,
                    "score": entry.get("score", 0.0),
                    "status": entry.get("status", "UNKNOWN"),
                    "role": cfg.get("role", "unknown"),
                    "location": cfg.get("location", "unknown"),
                    "user_count": int(cfg.get("user_count", 0)),
                    "revenue_per_min_inr": int(cfg.get("revenue_per_min_inr", 0)),
                }

    return affected


# ── AFFECTED SERVICE COMPUTATION ────────────────────────────────────────────
def compute_affected_services(
    affected_routers: Dict[str, Dict[str, Any]],
    service_map: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For each service, determine availability based on which of its
    host routers are affected.
    """
    services_cfg = service_map["services"]
    routers_cfg = service_map["routers"]

    affected_set = set(affected_routers.keys())

    out: List[Dict[str, Any]] = []

    for svc_id, svc in services_cfg.items():
        hosts: List[str] = []
        for rname, rcfg in routers_cfg.items():
            if svc_id in (rcfg.get("services_hosted") or []):
                hosts.append(rname)

        if not hosts:
            continue

        affected_hosts = [h for h in hosts if h in affected_set]
        healthy_hosts = [h for h in hosts if h not in affected_set]

        if not affected_hosts:
            continue  # service fully healthy, skip

        if healthy_hosts:
            availability = "DEGRADED"
        else:
            availability = "DOWN"

        out.append({
            "service_id": svc_id,
            "name": svc.get("name", svc_id),
            "criticality": svc.get("criticality", "medium"),
            "rbi_mandated": bool(svc.get("rbi_mandated", False)),
            "availability": availability,
            "sla_uptime_pct": svc.get("sla_uptime_pct", 0.0),
            "revenue_per_min_inr": int(svc.get("revenue_per_min_inr", 0)),
            "affected_hosts": affected_hosts,
            "healthy_hosts": healthy_hosts,
            "host_count_total": len(hosts),
        })

    # Sort by criticality, then DOWN before DEGRADED
    crit_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out.sort(key=lambda s: (
        crit_order.get(s["criticality"], 99),
        0 if s["availability"] == "DOWN" else 1,
    ))

    return out


# ── PROPAGATION CHAINS ──────────────────────────────────────────────────────
def compute_propagation_chains(
    affected_routers: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Group affected routers by root. A root is a directly-affected router
    with no affected upstream.
    """
    roots: Dict[str, List[str]] = {}
    for rname, entry in affected_routers.items():
        if entry["directly_affected"]:
            roots[rname] = []

    # Assign each non-root to its root (last in propagation_chain)
    for rname, entry in affected_routers.items():
        if entry["directly_affected"]:
            continue
        chain = entry.get("propagation_chain", [])
        if not chain:
            continue
        root = chain[0]
        if root in roots:
            roots[root].append(rname)

    out: List[Dict[str, Any]] = []
    for root, downstream in roots.items():
        total_downstream_users = sum(
            affected_routers[r]["user_count"] for r in downstream
        )
        out.append({
            "root": root,
            "downstream": sorted(downstream),
            "downstream_count": len(downstream),
            "total_users_downstream": total_downstream_users,
        })
    return out


# ── SEVERITY ────────────────────────────────────────────────────────────────
def compute_severity(
    affected_routers: Dict[str, Dict[str, Any]],
    affected_services: List[Dict[str, Any]],
) -> str:
    if not affected_routers:
        return "NONE"

    # HO failure cascades everywhere
    if "HO-Chennai" in affected_routers:
        return "CRITICAL"

    # RBI-critical service down?
    for svc in affected_services:
        if svc["criticality"] == "critical" and svc["availability"] == "DOWN":
            return "CRITICAL"

    # High-criticality service down?
    for svc in affected_services:
        if svc["criticality"] == "high" and svc["availability"] == "DOWN":
            return "HIGH"

    # Critical service degraded?
    for svc in affected_services:
        if svc["criticality"] == "critical" and svc["availability"] == "DEGRADED":
            return "HIGH"

    # High service degraded OR 3+ routers affected?
    if len(affected_routers) >= 3:
        return "HIGH"

    for svc in affected_services:
        if svc["criticality"] == "high" and svc["availability"] == "DEGRADED":
            return "MEDIUM"

    if len(affected_routers) >= 2:
        return "MEDIUM"

    return "LOW"


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_impact_report() -> Dict[str, Any]:
    health = load_json(HEALTH_PATH, required=True)
    service_map = load_json(SERVICE_MAP_PATH, required=True)

    affected_routers = compute_affected_routers(health, service_map)
    affected_services = compute_affected_services(affected_routers, service_map)
    propagation_chains = compute_propagation_chains(affected_routers)
    severity = compute_severity(affected_routers, affected_services)

    affected_user_count = sum(
        e["user_count"] for e in affected_routers.values()
    )
    affected_branch_count = sum(
        1 for e in affected_routers.values() if e["role"] == "branch"
    )
    revenue_per_min = sum(
        e["revenue_per_min_inr"] for e in affected_routers.values()
    )
    services_down = [s for s in affected_services if s["availability"] == "DOWN"]
    services_degraded = [s for s in affected_services if s["availability"] == "DEGRADED"]
    rbi_down = [s for s in services_down if s["rbi_mandated"]]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "health": str(HEALTH_PATH.relative_to(PROJECT_ROOT)),
            "service_map": str(SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)),
        },
        "source_health_timestamp": health.get("timestamp", ""),
        "source_network_status": health.get("network", {}).get("status", "UNKNOWN"),
        "overall_impact": {
            "severity": severity,
            "affected_router_count": len(affected_routers),
            "affected_service_count": len(affected_services),
            "services_down": len(services_down),
            "services_degraded": len(services_degraded),
            "rbi_mandated_services_down": len(rbi_down),
            "affected_user_count": affected_user_count,
            "affected_branch_count": affected_branch_count,
            "total_revenue_per_min_inr": revenue_per_min,
        },
        "affected_routers": affected_routers,
        "affected_services": affected_services,
        "propagation_chains": propagation_chains,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    ov = report["overall_impact"]
    print("─" * 78)
    print(f" SEVERITY           : {ov['severity']}")
    print(f" Affected routers   : {ov['affected_router_count']}")
    print(f" Affected services  : {ov['affected_service_count']} "
          f"(DOWN={ov['services_down']}, DEGRADED={ov['services_degraded']})")
    print(f" RBI services down  : {ov['rbi_mandated_services_down']}")
    print(f" Affected users     : {ov['affected_user_count']}")
    print(f" Affected branches  : {ov['affected_branch_count']}")
    print(f" Revenue @ risk/min : INR {ov['total_revenue_per_min_inr']:,}")
    print("─" * 78)

    if report["affected_routers"]:
        print(" AFFECTED ROUTERS")
        for rname, e in report["affected_routers"].items():
            tag = "DIRECT" if e["directly_affected"] else "PROPAGATED"
            chain = "->".join(e["propagation_chain"]) if e["propagation_chain"] else "?"
            print(
                f"  {rname:18s} {e['status']:8s} score={e['score']:5.1f}  "
                f"[{tag:10s}]  chain={chain}"
            )
        print("─" * 78)

    if report["affected_services"]:
        print(" AFFECTED SERVICES")
        for s in report["affected_services"]:
            rbi = "RBI" if s["rbi_mandated"] else "   "
            print(
                f"  [{rbi}] {s['service_id']:24s} "
                f"{s['availability']:9s} "
                f"crit={s['criticality']:8s} "
                f"hosts={len(s['affected_hosts'])}/{s['host_count_total']}"
            )
        print("─" * 78)

    if report["propagation_chains"]:
        print(" PROPAGATION CHAINS")
        for c in report["propagation_chains"]:
            if c["downstream"]:
                print(f"  {c['root']} -> {', '.join(c['downstream'])} "
                      f"({c['downstream_count']} routers, {c['total_users_downstream']} users)")
        print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia Impact Analyzer (B3)")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA IMPACT ANALYZER (B3)")
    print("=" * 78)
    print(f" Health      : {HEALTH_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Service map : {SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output      : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_impact_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        ov = report["overall_impact"]
        print(f"[INFO] Severity: {ov['severity']}  "
              f"routers={ov['affected_router_count']}  "
              f"services={ov['affected_service_count']}  "
              f"users={ov['affected_user_count']}")

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
