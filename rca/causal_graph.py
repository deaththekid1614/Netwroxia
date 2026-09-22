#!/usr/bin/env python3
"""
Netwroxia — Phase B6: Causal Graph Builder

Builds a directed acyclic graph for each correlated incident, showing
propagation from the root cause to downstream affected routers.

Inputs:
  - rca/latest_correlation.json  (B5)
  - impact/service_map.json      (B2)

Output: rca/latest_causal_graph.json

Also produces a Graphviz DOT string per graph so the dashboard can
render directly without extra processing.

Graph anatomy:
  - nodes = routers (affected + their immediate healthy children)
  - edges = upstream -> downstream propagation direction
  - only edges where downstream is affected are marked "active"
  - each graph carries a root_cause with confidence

Confidence = (0.5 + 0.1 * depth) * severity_multiplier, capped at 0.99

Consumed by B7 (historical_match), B8 (rca_output), dashboard (Phase E).

CLI:
    python3 rca/causal_graph.py
    python3 rca/causal_graph.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CORRELATION_PATH = PROJECT_ROOT / "rca" / "latest_correlation.json"
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
HEALTH_PATH = PROJECT_ROOT / "analytics" / "latest_health.json"
OUTPUT_PATH = PROJECT_ROOT / "rca" / "latest_causal_graph.json"

SEVERITY_MULT = {
    "CRITICAL": 1.00,
    "HIGH": 0.85,
    "MEDIUM": 0.70,
    "LOW": 0.50,
    "NONE": 0.00,
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Missing required file: {path}")
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


def build_children_map(service_map: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    service_map.propagation = {"A": ["B"], ...} means B is downstream of A.
    Return {"A": ["B"], "B": [...]}. Skip the "description" key.
    """
    propagation = service_map.get("propagation", {})
    return {
        src: list(dests)
        for src, dests in propagation.items()
        if src != "description"
    }


def find_depth(
    root: str,
    affected_set: set,
    children_map: Dict[str, List[str]],
) -> int:
    """
    Deepest propagation hop count from root through affected downstream
    only. Root alone = 0. Root -> one affected child = 1. Etc.
    """
    if root not in affected_set:
        return 0
    depths: List[int] = []
    for child in children_map.get(root, []):
        if child in affected_set:
            depths.append(1 + find_depth(child, affected_set, children_map))
    return max(depths) if depths else 0


def build_dot(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    root: str,
    title: str,
) -> str:
    """Emit a Graphviz DOT string for this graph."""
    lines = [f'digraph "{title}" {{']
    lines.append('  rankdir=TB;')
    lines.append('  node [shape=box style=rounded fontname="Helvetica"];')

    for n in nodes:
        color_map = {
            "OK":       "#4CAF50",
            "HEALTHY":  "#4CAF50",
            "DEGRADED": "#FFC107",
            "WARNING":  "#FFC107",
            "CRITICAL": "#F44336",
            "DOWN":     "#7B1FA2",
            "MISSING":  "#7B1FA2",
            "UNKNOWN":  "#9E9E9E",
        }
        color = color_map.get(n["status"], "#9E9E9E")
        border = "3.0" if n["id"] == root else "1.0"
        label = (
            f'{n["id"]}\\n'
            f'{n["role"]}\\n'
            f'score={n["health_score"]:.0f} status={n["status"]}'
        )
        lines.append(
            f'  "{n["id"]}" '
            f'[label="{label}" '
            f'fillcolor="{color}" style="filled,rounded" '
            f'penwidth={border}];'
        )

    for e in edges:
        style = "solid" if e["status"] == "active" else "dashed"
        color = "#D32F2F" if e["status"] == "active" else "#9E9E9E"
        lines.append(
            f'  "{e["source"]}" -> "{e["target"]}" '
            f'[style={style} color="{color}"];'
        )

    lines.append("}")
    return "\n".join(lines)


def build_graph_for_incident(
    incident: Dict[str, Any],
    children_map: Dict[str, List[str]],
    health_routers: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    root = incident["root"]
    affected = set(incident["affected_routers"])

    # Collect nodes: affected routers + their immediate healthy children
    node_ids = set(affected)
    for r in affected:
        for child in children_map.get(r, []):
            node_ids.add(child)

    nodes: List[Dict[str, Any]] = []
    for rid in sorted(node_ids):
        hr = health_routers.get(rid, {})
        is_affected = rid in affected
        nodes.append({
            "id": rid,
            "label": rid,
            "role": hr.get("role", "unknown"),
            "location": "",
            "status": hr.get("status", "UNKNOWN"),
            "health_score": float(hr.get("score", 0.0)),
            "affected": is_affected,
            "user_count": int(hr.get("user_count", 0)),
            "revenue_per_min_inr": int(
                incident["total_revenue_per_min_inr"]
                if rid == root else 0
            ),
        })

    # Enrich node revenue from B1 health if available
    for n in nodes:
        hr = health_routers.get(n["id"], {})
        n["revenue_per_min_inr"] = int(hr.get("user_count", 0) * 250)  # fallback placeholder

    # Build edges: src -> child, active if child is affected
    edges: List[Dict[str, Any]] = []
    for rid in sorted(node_ids):
        for child in children_map.get(rid, []):
            if child not in node_ids:
                continue
            active = (child in affected)
            edges.append({
                "source": rid,
                "target": child,
                "direction": "propagation",
                "status": "active" if active else "inactive",
            })

    depth = find_depth(root, affected, children_map)
    severity_mult = SEVERITY_MULT.get(incident["severity"], 0.0)
    confidence = min(0.99, round((0.5 + 0.1 * depth) * severity_mult, 3))

    graph_id = incident["incident_id"]
    dot = build_dot(nodes, edges, root, graph_id)

    return {
        "graph_id": graph_id,
        "incident_id": graph_id,
        "title": f"Incident {graph_id} — root {root}",
        "root_cause": {
            "router": root,
            "role": incident["root_role"],
            "severity": incident["severity"],
            "confidence": confidence,
            "propagation_depth": depth,
            "affected_count": incident["affected_routers_count"],
        },
        "nodes": nodes,
        "edges": edges,
        "dot": dot,
    }


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_causal_graph_report() -> Dict[str, Any]:
    correlation = load_json(CORRELATION_PATH, required=True)
    service_map = load_json(SERVICE_MAP_PATH, required=True)
    health = load_json(HEALTH_PATH, required=False) or {}

    children_map = build_children_map(service_map)
    health_routers = health.get("routers", {})

    graphs: List[Dict[str, Any]] = []
    for incident in correlation.get("incidents", []):
        graphs.append(
            build_graph_for_incident(incident, children_map, health_routers)
        )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "correlation": str(CORRELATION_PATH.relative_to(PROJECT_ROOT)),
            "service_map": str(SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)),
            "health": str(HEALTH_PATH.relative_to(PROJECT_ROOT)),
        },
        "source_correlation_timestamp": correlation.get("timestamp", ""),
        "summary": {
            "graph_count": len(graphs),
            "highest_confidence": (
                max(g["root_cause"]["confidence"] for g in graphs)
                if graphs else 0.0
            ),
        },
        "graphs": graphs,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print("─" * 78)
    print(f" GRAPHS            : {s['graph_count']}")
    print(f" Highest confidence: {s['highest_confidence']:.3f}")
    print("─" * 78)

    if not report["graphs"]:
        print(" No causal graphs (no active incidents).")
        print("─" * 78)
        return

    for g in report["graphs"]:
        rc = g["root_cause"]
        print(f" GRAPH {g['graph_id']}  [{rc['severity']}]")
        print(f"   root          : {rc['router']} ({rc['role']})")
        print(f"   confidence    : {rc['confidence']:.3f}")
        print(f"   depth         : {rc['propagation_depth']}")
        print(f"   affected      : {rc['affected_count']} routers")
        print(f"   nodes         : {len(g['nodes'])}")
        print(f"   edges         : {len(g['edges'])} "
              f"(active={sum(1 for e in g['edges'] if e['status'] == 'active')})")
        # Show a mini textual tree
        print("   -- propagation --")
        for e in g["edges"]:
            marker = "->" if e["status"] == "active" else "-x"
            print(f"      {e['source']:18s} {marker} {e['target']:18s} "
                  f"[{e['status']}]")
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Causal Graph Builder (B6)"
    )
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--dot", type=str, default=None,
                        help="Print DOT for a specific graph_id and exit")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA CAUSAL GRAPH BUILDER (B6)")
    print("=" * 78)
    print(f" Correlation input : {CORRELATION_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Service map       : {SERVICE_MAP_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Health input      : {HEALTH_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Output            : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_causal_graph_report()
    save_json(OUTPUT_PATH, report)

    if args.dot:
        match = next(
            (g for g in report["graphs"] if g["graph_id"] == args.dot),
            None
        )
        if match:
            print(match["dot"])
            return
        print(f"[WARN] No graph with id {args.dot}")
        sys.exit(1)

    if args.show:
        print_summary(report)
    else:
        s = report["summary"]
        print(
            f"[INFO] Graphs: {s['graph_count']}  "
            f"highest_confidence={s['highest_confidence']:.3f}"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
