#!/usr/bin/env python3
"""
Netwroxia — Phase B8: RCA Output Assembler (Phase B final artifact)

Consolidates B1/B3/B4/B5/B6/B7 outputs into a single RCA document:
  - Per-incident root cause with combined confidence
  - Propagation chain from B6's causal graph
  - Business impact from B4
  - Historical match from B7 with past resolution
  - Human-readable narrative (template-driven, no LLM)

Inputs:
  - analytics/latest_health.json              (B1)
  - impact/latest_impact.json                 (B3)
  - impact/latest_business_impact.json        (B4)
  - rca/latest_correlation.json               (B5)
  - rca/latest_causal_graph.json              (B6)
  - rca/latest_historical_match.json          (B7)

Output: rca/latest_rca.json

Consumed by: Phase C (remediation), Stage 4 copilot, Phase E dashboard.

CLI:
    python3 rca/rca_output.py
    python3 rca/rca_output.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

HEALTH_PATH = PROJECT_ROOT / "analytics" / "latest_health.json"
IMPACT_PATH = PROJECT_ROOT / "impact" / "latest_impact.json"
BIZ_IMPACT_PATH = PROJECT_ROOT / "impact" / "latest_business_impact.json"
CORRELATION_PATH = PROJECT_ROOT / "rca" / "latest_correlation.json"
CAUSAL_GRAPH_PATH = PROJECT_ROOT / "rca" / "latest_causal_graph.json"
HISTORICAL_MATCH_PATH = PROJECT_ROOT / "rca" / "latest_historical_match.json"
OUTPUT_PATH = PROJECT_ROOT / "rca" / "latest_rca.json"

# Combined confidence weight: B6 root confidence vs B7 historical match
CONF_WEIGHT_GRAPH = 0.6
CONF_WEIGHT_HISTORY = 0.4


# ── HELPERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = False) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Missing required file: {path}")
            sys.exit(1)
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[WARN] Malformed JSON in {path}: {e}")
        return None


def save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def format_inr(n: float) -> str:
    n = float(n or 0)
    if n == 0:
        return "₹0"
    a = abs(n)
    if a < 1000:
        return f"₹{int(n)}"
    if a < 100_000:
        return f"₹{n/1000:.1f}K"
    if a < 10_000_000:
        return f"₹{n/100_000:.2f}L"
    return f"₹{n/10_000_000:.2f}Cr"


def find_incident_chain(graph: Dict[str, Any], root: str) -> List[str]:
    """BFS from root through active edges to build the affected chain."""
    edges = graph.get("edges", [])
    children: Dict[str, List[str]] = {}
    for e in edges:
        if e.get("status") == "active":
            children.setdefault(e["source"], []).append(e["target"])

    chain: List[str] = [root]
    queue = [root]
    seen = {root}
    while queue:
        cur = queue.pop(0)
        for child in children.get(cur, []):
            if child not in seen:
                seen.add(child)
                chain.append(child)
                queue.append(child)
    return chain


def build_narrative(
    root: str,
    root_role: str,
    severity: str,
    confidence: float,
    chain: List[str],
    affected_users: int,
    affected_services_count: int,
    loss_per_min: float,
    rbi_penalty: float,
    past_match: Optional[Dict[str, Any]],
) -> str:
    role_label = {
        "head_office": "Head Office",
        "zonal_office": "Zonal Office",
        "branch": "Branch",
    }.get(root_role, "Router")

    parts: List[str] = []

    parts.append(
        f"{role_label} {root} is the probable root cause of the current "
        f"{severity}-severity incident (confidence {int(round(confidence*100))}%)."
    )

    if len(chain) > 1:
        downstream = ", ".join(chain[1:])
        parts.append(
            f"Failure propagation has reached {len(chain) - 1} downstream "
            f"router(s): {downstream}."
        )
    else:
        parts.append("No downstream propagation detected — this is an isolated node failure.")

    if affected_users > 0:
        parts.append(
            f"Approximately {affected_users:,} users are affected across "
            f"{affected_services_count} banking service(s)."
        )

    if loss_per_min > 0:
        parts.append(
            f"Current loss rate is {format_inr(loss_per_min)} per minute; "
            f"RBI penalty exposure estimated at {format_inr(rbi_penalty)} "
            f"if unrecovered within the compliance window."
        )

    if past_match:
        mid = past_match.get("past_incident_id", "unknown")
        msc = past_match.get("match_score", 0)
        cause = past_match.get("past_root_cause", "")
        resolution = past_match.get("past_resolution", "")
        rec_time = past_match.get("past_recovery_time_min", 0)
        parts.append(
            f"Historical precedent: {mid} (similarity {int(round(msc*100))}%). "
            f"Past root cause: {cause} "
            f"Recorded resolution: {resolution} "
            f"(recovery in {rec_time} min)."
        )
    else:
        parts.append(
            "No sufficiently similar past incident was found in the corpus; "
            "treat this as a novel failure pattern."
        )

    return " ".join(parts)


# ── INCIDENT ASSEMBLY ───────────────────────────────────────────────────────
def assemble_incident(
    correlation_incident: Dict[str, Any],
    graph: Optional[Dict[str, Any]],
    historical_match: Optional[Dict[str, Any]],
    impact: Dict[str, Any],
    biz_impact: Dict[str, Any],
) -> Dict[str, Any]:
    incident_id = correlation_incident["incident_id"]
    root = correlation_incident["root"]
    root_role = correlation_incident.get("root_role", "unknown")
    severity = correlation_incident.get("severity", "UNKNOWN")

    # Confidence: blend B6 root confidence + B7 top match score
    graph_conf = float(graph["root_cause"]["confidence"]) if graph else 0.0
    hist_score = 0.0
    top_match: Optional[Dict[str, Any]] = None
    if historical_match and historical_match.get("matches"):
        top_match = historical_match["matches"][0]
        hist_score = float(top_match.get("match_score", 0.0))

    if graph and top_match:
        confidence = round(
            CONF_WEIGHT_GRAPH * graph_conf + CONF_WEIGHT_HISTORY * hist_score,
            3,
        )
    elif graph:
        confidence = round(graph_conf, 3)
    elif top_match:
        confidence = round(hist_score, 3)
    else:
        confidence = 0.0

    # Propagation chain
    if graph:
        chain = find_incident_chain(graph, root)
        propagation_depth = int(graph["root_cause"].get("propagation_depth", 0))
    else:
        chain = [root]
        propagation_depth = 0

    # Attach services affected to this incident from B3 impact
    affected_services: List[Dict[str, Any]] = []
    for svc in impact.get("affected_services", []):
        hosts = svc.get("affected_hosts", [])
        if any(h in chain for h in hosts):
            affected_services.append({
                "service_id": svc.get("service_id"),
                "name": svc.get("name"),
                "availability": svc.get("availability"),
                "criticality": svc.get("criticality"),
                "rbi_mandated": svc.get("rbi_mandated", False),
            })

    affected_users = int(correlation_incident.get("total_users_affected", 0))
    revenue_per_min = int(correlation_incident.get("total_revenue_per_min_inr", 0))

    # Business impact — pull directly from B4 (single global figure; per-incident
    # attribution here uses this incident's own revenue if we have it)
    loss_per_min = revenue_per_min if revenue_per_min > 0 else int(
        biz_impact.get("current_loss", {}).get("per_minute_inr", 0)
    )
    rbi_penalty = int(biz_impact.get("rbi_compliance", {}).get("estimated_penalty_inr", 0))

    narrative = build_narrative(
        root=root,
        root_role=root_role,
        severity=severity,
        confidence=confidence,
        chain=chain,
        affected_users=affected_users,
        affected_services_count=len(affected_services),
        loss_per_min=loss_per_min,
        rbi_penalty=rbi_penalty,
        past_match=top_match,
    )

    historical_block: Optional[Dict[str, Any]] = None
    if top_match:
        historical_block = {
            "top_match_id": top_match.get("past_incident_id"),
            "top_match_score": top_match.get("match_score"),
            "past_severity": top_match.get("past_severity"),
            "past_root_cause": top_match.get("past_root_cause"),
            "past_resolution": top_match.get("past_resolution"),
            "past_recovery_time_min": top_match.get("past_recovery_time_min"),
            "past_rbi_reportable": top_match.get("past_rbi_reportable"),
            "runner_up_id": (
                historical_match["matches"][1].get("past_incident_id")
                if historical_match and len(historical_match["matches"]) > 1
                else None
            ),
        }

    return {
        "incident_id": incident_id,
        "summary": {
            "root": root,
            "root_role": root_role,
            "severity": severity,
            "confidence": confidence,
            "propagation_depth": propagation_depth,
            "chain": chain,
            "affected_count": len(chain),
        },
        "root_cause": {
            "router": root,
            "role": root_role,
            "location": correlation_incident.get("root_location", "unknown"),
            "graph_confidence": round(graph_conf, 3),
            "historical_match_score": round(hist_score, 3),
            "combined_confidence": confidence,
        },
        "propagation": {
            "chain": chain,
            "depth": propagation_depth,
            "downstream_count": max(0, len(chain) - 1),
            "graph_id": graph.get("graph_id") if graph else None,
        },
        "impact": {
            "severity": severity,
            "affected_routers": chain,
            "affected_services": affected_services,
            "affected_users": affected_users,
            "revenue_per_min_inr": loss_per_min,
        },
        "business": {
            "loss_per_min_inr": loss_per_min,
            "loss_per_min_display": format_inr(loss_per_min),
            "rbi_penalty_inr": rbi_penalty,
            "rbi_penalty_display": format_inr(rbi_penalty),
        },
        "historical": historical_block,
        "narrative": narrative,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_rca_report() -> Dict[str, Any]:
    health = load_json(HEALTH_PATH)
    impact = load_json(IMPACT_PATH)
    biz_impact = load_json(BIZ_IMPACT_PATH) or {}
    correlation = load_json(CORRELATION_PATH)
    causal_graph = load_json(CAUSAL_GRAPH_PATH)
    historical = load_json(HISTORICAL_MATCH_PATH)

    correlation_incidents = (correlation or {}).get("incidents", [])
    graphs_by_id = {
        g["graph_id"]: g
        for g in (causal_graph or {}).get("graphs", [])
    }
    matches_by_id = {
        m["current_incident_id"]: m
        for m in (historical or {}).get("matches", [])
    }

    incidents_out: List[Dict[str, Any]] = []
    for corr_inc in correlation_incidents:
        iid = corr_inc["incident_id"]
        graph = graphs_by_id.get(iid)
        hist = matches_by_id.get(iid)
        incident = assemble_incident(
            correlation_incident=corr_inc,
            graph=graph,
            historical_match=hist,
            impact=impact or {},
            biz_impact=biz_impact,
        )
        incidents_out.append(incident)

    # Sort: CRITICAL first, then by confidence
    sev_rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
    incidents_out.sort(
        key=lambda i: (
            -sev_rank.get(i["summary"]["severity"], 0),
            -i["summary"]["confidence"],
        )
    )

    network_status = (health or {}).get("network", {}).get("status", "UNKNOWN")
    network_score = (health or {}).get("network", {}).get("score", 0.0)

    top_incident = incidents_out[0] if incidents_out else None

    overall_narrative = ""
    if not incidents_out:
        overall_narrative = (
            f"Network is {network_status} (score {network_score:.1f}/100). "
            f"No active incidents; no RCA required."
        )
    else:
        overall_narrative = (
            f"{len(incidents_out)} active incident(s) detected. "
            f"Top: {top_incident['summary']['severity']} at "
            f"{top_incident['summary']['root']} "
            f"(confidence {int(round(top_incident['summary']['confidence']*100))}%)."
        )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "health": str(HEALTH_PATH.relative_to(PROJECT_ROOT)),
            "impact": str(IMPACT_PATH.relative_to(PROJECT_ROOT)),
            "business_impact": str(BIZ_IMPACT_PATH.relative_to(PROJECT_ROOT)),
            "correlation": str(CORRELATION_PATH.relative_to(PROJECT_ROOT)),
            "causal_graph": str(CAUSAL_GRAPH_PATH.relative_to(PROJECT_ROOT)),
            "historical_match": str(HISTORICAL_MATCH_PATH.relative_to(PROJECT_ROOT)),
        },
        "system_healthy": len(incidents_out) == 0,
        "network_status": network_status,
        "network_score": network_score,
        "summary": {
            "incident_count": len(incidents_out),
            "critical_count": sum(
                1 for i in incidents_out if i["summary"]["severity"] == "CRITICAL"
            ),
            "high_count": sum(
                1 for i in incidents_out if i["summary"]["severity"] == "HIGH"
            ),
            "top_incident_id": top_incident["incident_id"] if top_incident else None,
            "top_root": top_incident["summary"]["root"] if top_incident else None,
        },
        "overall_narrative": overall_narrative,
        "incidents": incidents_out,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    print("─" * 78)
    print(f" NETWORK STATUS   : {report['network_status']} ({report['network_score']:.1f}/100)")
    print(f" SYSTEM HEALTHY   : {report['system_healthy']}")
    print(f" INCIDENTS        : {report['summary']['incident_count']}  "
          f"(CRITICAL={report['summary']['critical_count']}, "
          f"HIGH={report['summary']['high_count']})")
    print("─" * 78)
    print(f" {report['overall_narrative']}")
    print("─" * 78)

    if not report["incidents"]:
        return

    for inc in report["incidents"]:
        s = inc["summary"]
        b = inc["business"]
        h = inc["historical"]
        print(f" [{s['severity']:8s}] {inc['incident_id']}  "
              f"root={s['root']} ({s['root_role']})  "
              f"confidence={int(round(s['confidence']*100))}%")
        print(f"   chain        : {' -> '.join(s['chain'])}")
        print(f"   impact       : {inc['impact']['affected_users']} users, "
              f"{len(inc['impact']['affected_services'])} services, "
              f"{b['loss_per_min_display']}/min")
        print(f"   rbi penalty  : {b['rbi_penalty_display']}")
        if h:
            print(f"   hist match   : {h['top_match_id']}  "
                  f"({int(round(h['top_match_score']*100))}%)  "
                  f"past recovery={h['past_recovery_time_min']}min")
        print(f"   narrative    : {inc['narrative'][:140]}...")
        print()


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia RCA Output Assembler (B8)"
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA RCA OUTPUT ASSEMBLER (B8)")
    print("=" * 78)
    print(f" Output : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_rca_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        s = report["summary"]
        print(
            f"[INFO] incidents={s['incident_count']}  "
            f"CRITICAL={s['critical_count']}  HIGH={s['high_count']}  "
            f"healthy={report['system_healthy']}"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
