#!/usr/bin/env python3
"""
Netwroxia — Phase B5 v2: RCA Correlation Engine

Reads B3's impact report and groups affected routers into discrete
incidents, each with an identified root cause.

IMPORTANT: This engine does NOT trust B3's propagation_chains. B3 marks
a router "directly affected" whenever its own beacon/ML says so — which
happens for EVERY router when an upstream interface is degraded. This
engine walks the physical propagation graph (from service_map) to find
the true topmost affected ancestor for each affected router.

Answers: "Are these multiple symptoms one incident or many?"

Output: rca/latest_correlation.json

Design:
  - Affected routers = all in B3's affected_routers (direct + propagated)
  - For each affected router, walk UP the propagation graph until no
    more affected ancestors remain. That topmost ancestor is the root.
  - Group affected routers by their topmost ancestor -> one incident per root
  - Signature = deterministic hash of (root + sorted members)
  - Severity = role weight + worst status + downstream count + revenue

Consumed by B6 (causal_graph), B7 (historical_matcher), B8 (rca_output).

CLI:
    python3 rca/correlation_engine.py
    python3 rca/correlation_engine.py --show
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

IMPACT_PATH = PROJECT_ROOT / "impact" / "latest_impact.json"
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
OUTPUT_PATH = PROJECT_ROOT / "rca" / "latest_correlation.json"


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


def make_signature(root: str, members: List[str]) -> str:
    key = root + "|" + "|".join(sorted(members))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def build_upstream_map(service_map: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Invert propagation map.
    service_map.propagation = {"A": ["B"], "B": ["C"]}  (A upstream of B)
    upstream = {"B": ["A"], "C": ["B"], "A": [], ...}
    """
    propagation = service_map.get("propagation", {})
    upstream: Dict[str, List[str]] = {}
    for src, dests in propagation.items():
        if src == "description":
            continue
        upstream.setdefault(src, [])
        for dst in dests:
            upstream.setdefault(dst, []).append(src)
    return upstream


def find_topmost_affected_ancestor(
    router: str,
    affected_set: Set[str],
    upstream_map: Dict[str, List[str]],
) -> str:
    """
    Walk up the propagation graph while ancestors are affected.
    Return the highest ancestor that is itself affected, or the router
    itself if no affected ancestor exists.
    """
    visited: Set[str] = set()
    current = router

    while True:
        if current in visited:
            break  # cycle protection
        visited.add(current)

        ancestors = [a for a in upstream_map.get(current, []) if a in affected_set]
        if not ancestors:
            return current

        # Linear topology assumption: pick first ancestor.
        # For real topologies with branching upstreams, this would need BFS.
        current = ancestors[0]

    return current


def severity_from_score(score: float) -> str:
    if score >= 70:
        return "CRITICAL"
    if score >= 45:
        return "HIGH"
    if score >= 20:
        return "MEDIUM"
    if score >= 5:
        return "LOW"
    return "NONE"


def compute_incident_severity(
    root_role: str,
    worst_status: str,
    downstream_count: int,
    total_revenue_inr: int,
) -> str:
    # Hard rule: HO + CRITICAL/DOWN is always CRITICAL
    if root_role == "head_office" and worst_status in {"CRITICAL", "DOWN"}:
        return "CRITICAL"

    score = 0
    role_pts = {"head_office": 40, "zonal_office": 20, "branch": 5}
    status_pts = {"DOWN": 30, "CRITICAL": 20, "WARNING": 10, "HEALTHY": 0}
    score += role_pts.get(root_role, 5)
    score += status_pts.get(worst_status, 0)
    score += 10 * downstream_count
    score += min(total_revenue_inr / 100_000, 30)

    return severity_from_score(score)


def worst_of(statuses: List[str]) -> str:
    order = ["DOWN", "CRITICAL", "WARNING", "HEALTHY", "UNKNOWN"]
    for s in order:
        if s in statuses:
            return s
    return "UNKNOWN"


# ── CORRELATION ─────────────────────────────────────────────────────────────
def correlate_incidents(
    affected_routers: Dict[str, Dict[str, Any]],
    service_map: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Compute correlation ourselves (don't trust B3's propagation_chains).
    """
    routers_cfg = service_map.get("routers", {})
    upstream_map = build_upstream_map(service_map)

    affected_set = set(affected_routers.keys())
    if not affected_set:
        return []

    # Map each affected router -> its topmost affected ancestor (root)
    root_of: Dict[str, str] = {}
    for r in affected_set:
        root_of[r] = find_topmost_affected_ancestor(r, affected_set, upstream_map)

    # Group by root
    groups: Dict[str, List[str]] = {}
    for r, root in root_of.items():
        groups.setdefault(root, []).append(r)

    # Build incidents
    incidents: List[Dict[str, Any]] = []
    for root, members in groups.items():
        members_sorted = sorted(members, key=lambda x: (
            0 if x == root else 1,
            x
        ))
        downstream = [m for m in members_sorted if m != root]

        root_cfg = routers_cfg.get(root, {})
        root_role = root_cfg.get("role", "branch")

        statuses = [
            affected_routers.get(m, {}).get("status", "UNKNOWN")
            for m in members_sorted
        ]
        worst = worst_of(statuses)

        total_users = sum(
            affected_routers.get(m, {}).get("user_count", 0)
            for m in members_sorted
        )
        total_revenue = sum(
            affected_routers.get(m, {}).get("revenue_per_min_inr", 0)
            for m in members_sorted
        )

        severity = compute_incident_severity(
            root_role, worst, len(downstream), total_revenue
        )

        # Severity score for sorting (higher = more severe)
        sev_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
        severity_score = (
            sev_rank[severity] * 1_000_000
            + total_revenue
            + total_users * 100
        )

        # Directly affected per B3 (informational)
        direct_flags = {
            m: bool(affected_routers.get(m, {}).get("directly_affected", False))
            for m in members_sorted
        }

        incidents.append({
            "incident_id": make_signature(root, members_sorted),
            "root": root,
            "root_role": root_role,
            "root_location": root_cfg.get("location", "unknown"),
            "affected_routers": members_sorted,
            "downstream_routers": downstream,
            "affected_routers_count": len(members_sorted),
            "downstream_count": len(downstream),
            "total_users_affected": total_users,
            "total_revenue_per_min_inr": int(total_revenue),
            "statuses": statuses,
            "worst_status": worst,
            "severity": severity,
            "severity_score": round(severity_score, 2),
            "directly_affected_flags": direct_flags,
        })

    # Sort: severity rank desc, then revenue desc
    incidents.sort(
        key=lambda i: (
            -{"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}[i["severity"]],
            -i["total_revenue_per_min_inr"],
        )
    )

    return incidents


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_correlation_report() -> Dict[str, Any]:
    impact = load_json(IMPACT_PATH, required=True)
    service_map = load_json(SERVICE_MAP_PATH, required=True)

    affected_routers = impact.get("affected_routers", {})
    incidents = correlate_incidents(affected_routers, service_map)

    severity_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "NONE": 0}
    for inc in incidents:
        severity_counts[inc["severity"]] += 1

    total_affected = sum(i["affected_routers_count"] for i in incidents)
    total_users = sum(i["total_users_affected"] for i in incidents)
    total_revenue = sum(i["total_revenue_per_min_inr"] for i in incidents)
    top = incidents[0] if incidents else None

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "impact": str(IMPACT_PATH.relative_to(PROJECT_ROOT)),
            "service_map": str(SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)),
        },
        "source_impact_timestamp": impact.get("timestamp", ""),
        "source_severity": impact.get("overall_impact", {}).get("severity", "UNKNOWN"),
        "summary": {
            "incident_count": len(incidents),
            "severity_counts": severity_counts,
            "total_affected_routers": total_affected,
            "total_users_affected": total_users,
            "total_revenue_per_min_inr": int(total_revenue),
            "has_critical_incident": severity_counts["CRITICAL"] > 0,
            "top_incident_id": top["incident_id"] if top else None,
            "top_root": top["root"] if top else None,
        },
        "incidents": incidents,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print("─" * 78)
    print(f" SEVERITY (B3)     : {report['source_severity']}")
    print(f" INCIDENTS         : {s['incident_count']}")
    sc = s["severity_counts"]
    print(f"   CRITICAL/HIGH/MEDIUM/LOW/NONE : "
          f"{sc['CRITICAL']}/{sc['HIGH']}/{sc['MEDIUM']}/{sc['LOW']}/{sc['NONE']}")
    print(f" Affected routers  : {s['total_affected_routers']}")
    print(f" Affected users    : {s['total_users_affected']}")
    print(f" Revenue @ risk/min: INR {s['total_revenue_per_min_inr']:,}")
    print("─" * 78)

    if not report["incidents"]:
        print(" No incidents detected.")
        print("─" * 78)
        return

    for inc in report["incidents"]:
        chain = " -> ".join(inc["affected_routers"])
        print(
            f" [{inc['severity']:8s}] {inc['incident_id']}  "
            f"root={inc['root']:18s} ({inc['root_role']})"
        )
        print(f"   chain        : {chain}")
        print(
            f"   downstream   : {inc['downstream_count']} routers  "
            f"users={inc['total_users_affected']}  "
            f"revenue={inc['total_revenue_per_min_inr']:,}/min  "
            f"worst={inc['worst_status']}"
        )
        print(f"   statuses     : {inc['statuses']}")
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia RCA Correlation Engine (B5 v2)"
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA RCA CORRELATION ENGINE (B5 v2)")
    print("=" * 78)
    print(f" Impact input : {IMPACT_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Service map  : {SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output       : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_correlation_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        s = report["summary"]
        print(
            f"[INFO] Incidents: {s['incident_count']}  "
            f"CRITICAL={s['severity_counts']['CRITICAL']}  "
            f"HIGH={s['severity_counts']['HIGH']}  "
            f"affected_routers={s['total_affected_routers']}"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
