#!/usr/bin/env python3
"""
Netwroxia — Phase B7: Historical Incident Matcher

Matches current incidents (from B6's causal graph) against a corpus of
past incidents. Produces top-N matches with weighted similarity scores
and root cause insights from history.

Inputs:
  - rca/latest_causal_graph.json                                  (B6)
  - copilot/knowledge_base/past_incidents/*.json                  (corpus)

Output: rca/latest_historical_match.json

Scoring (weighted sum, capped at 1.0):
  root_router match      0.35
  root_role match        0.15
  propagation_depth      0.10  (1 - min(|Δ|, 3)/3)
  affected_routers Jaccard 0.20
  affected_services Jaccard 0.15
  severity match         0.05

Consumed by B8 (rca_output) and Stage 4 copilot (for grounding).

CLI:
    python3 rca/historical_matcher.py
    python3 rca/historical_matcher.py --show
    python3 rca/historical_matcher.py --top 5
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CAUSAL_GRAPH_PATH = PROJECT_ROOT / "rca" / "latest_causal_graph.json"
PAST_INCIDENTS_DIR = PROJECT_ROOT / "copilot" / "knowledge_base" / "past_incidents"
OUTPUT_PATH = PROJECT_ROOT / "rca" / "latest_historical_match.json"

DEFAULT_TOP_N = 3
MATCH_THRESHOLD = 0.30  # below this, drop the match entirely

WEIGHTS = {
    "root_router": 0.35,
    "root_role": 0.15,
    "propagation_depth": 0.10,
    "affected_routers": 0.20,
    "affected_services": 0.15,
    "severity": 0.05,
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] Missing required file: {path}")
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


def load_past_incidents() -> List[Dict[str, Any]]:
    if not PAST_INCIDENTS_DIR.exists():
        print(f"[WARN] Past incidents dir not found: {PAST_INCIDENTS_DIR}")
        return []
    incidents = []
    for path in sorted(PAST_INCIDENTS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            if "incident_id" not in data:
                print(f"[WARN] Skipping {path.name}: no incident_id")
                continue
            data["_source_file"] = path.name
            incidents.append(data)
        except Exception as e:
            print(f"[WARN] Could not load {path.name}: {e}")
    return incidents


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def depth_closeness(d1: int, d2: int) -> float:
    return 1.0 - min(abs(d1 - d2), 3) / 3.0


def build_current_incident_summary(graph: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a B6 causal graph into the incident-summary shape used for
    matching against past incidents.
    """
    rc = graph.get("root_cause", {})
    affected_routers = [n["id"] for n in graph.get("nodes", []) if n.get("affected")]
    return {
        "incident_id": graph.get("graph_id", "unknown"),
        "severity": rc.get("severity", "UNKNOWN"),
        "root_router": rc.get("router", "unknown"),
        "root_role": rc.get("role", "unknown"),
        "propagation_depth": int(rc.get("propagation_depth", 0)),
        "affected_routers": affected_routers,
        # affected_services not in B6 graph — best-effort empty. B8 can
        # enrich if needed; scoring treats it as 0 contribution when both empty.
        "affected_services": [],
    }


# ── MATCHING ────────────────────────────────────────────────────────────────
def score_match(
    current: Dict[str, Any],
    past: Dict[str, Any],
) -> Tuple[float, Dict[str, float]]:
    components: Dict[str, float] = {}

    components["root_router"] = 1.0 if current["root_router"] == past.get("root_router") else 0.0
    components["root_role"] = 1.0 if current["root_role"] == past.get("root_role") else 0.0
    components["propagation_depth"] = depth_closeness(
        current["propagation_depth"], int(past.get("propagation_depth", 0))
    )
    components["affected_routers"] = jaccard(
        set(current["affected_routers"]),
        set(past.get("affected_routers", [])),
    )
    components["affected_services"] = jaccard(
        set(current["affected_services"]),
        set(past.get("affected_services", [])),
    )
    components["severity"] = 1.0 if current["severity"] == past.get("severity") else 0.0

    total = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    return round(total, 4), {k: round(v, 4) for k, v in components.items()}


def match_incident(
    current: Dict[str, Any],
    past_corpus: List[Dict[str, Any]],
    top_n: int,
) -> List[Dict[str, Any]]:
    scored = []
    for past in past_corpus:
        total, components = score_match(current, past)
        if total < MATCH_THRESHOLD:
            continue
        scored.append({
            "match_score": total,
            "component_scores": components,
            "past_incident_id": past.get("incident_id", "unknown"),
            "past_title": past.get("title", ""),
            "past_severity": past.get("severity", "UNKNOWN"),
            "past_root_cause": past.get("root_cause", ""),
            "past_resolution": past.get("resolution", ""),
            "past_recovery_time_min": past.get("recovery_time_min", 0),
            "past_rbi_reportable": bool(past.get("rbi_reportable", False)),
            "past_occurred_at": past.get("occurred_at", ""),
            "past_source_file": past.get("_source_file", ""),
        })
    scored.sort(key=lambda m: -m["match_score"])
    return scored[:top_n]


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_match_report(top_n: int) -> Dict[str, Any]:
    causal = load_json(CAUSAL_GRAPH_PATH, required=True)
    past_corpus = load_past_incidents()

    if not past_corpus:
        print("[WARN] Past incident corpus is empty. No matches produced.")
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_files": {
                "causal_graph": str(CAUSAL_GRAPH_PATH.relative_to(PROJECT_ROOT)),
                "past_incidents_dir": str(PAST_INCIDENTS_DIR.relative_to(PROJECT_ROOT)),
            },
            "corpus_size": 0,
            "summary": {
                "current_incident_count": len(causal.get("graphs", [])),
                "incidents_with_matches": 0,
                "highest_match_score": 0.0,
            },
            "matches": [],
        }

    results: List[Dict[str, Any]] = []
    highest = 0.0

    for graph in causal.get("graphs", []):
        current = build_current_incident_summary(graph)
        matches = match_incident(current, past_corpus, top_n)
        top_score = matches[0]["match_score"] if matches else 0.0
        highest = max(highest, top_score)

        results.append({
            "current_incident_id": current["incident_id"],
            "current_root": current["root_router"],
            "current_severity": current["severity"],
            "current_propagation_depth": current["propagation_depth"],
            "current_affected_routers": current["affected_routers"],
            "matches": matches,
            "match_count": len(matches),
            "top_match_score": top_score,
        })

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "causal_graph": str(CAUSAL_GRAPH_PATH.relative_to(PROJECT_ROOT)),
            "past_incidents_dir": str(PAST_INCIDENTS_DIR.relative_to(PROJECT_ROOT)),
        },
        "corpus_size": len(past_corpus),
        "summary": {
            "current_incident_count": len(causal.get("graphs", [])),
            "incidents_with_matches": sum(1 for r in results if r["match_count"] > 0),
            "highest_match_score": round(highest, 4),
        },
        "matches": results,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print("─" * 78)
    print(f" Corpus size       : {report['corpus_size']} past incidents")
    print(f" Current incidents : {s['current_incident_count']}")
    print(f" With matches      : {s['incidents_with_matches']}")
    print(f" Highest match     : {s['highest_match_score']:.4f}")
    print("─" * 78)

    if not report["matches"]:
        print(" No current incidents to match.")
        print("─" * 78)
        return

    for entry in report["matches"]:
        print(
            f" INCIDENT {entry['current_incident_id']}  "
            f"root={entry['current_root']}  "
            f"severity={entry['current_severity']}"
        )
        if not entry["matches"]:
            print("   (no historical matches above threshold)")
            continue
        for i, m in enumerate(entry["matches"], 1):
            print(
                f"   #{i}  score={m['match_score']:.4f}  "
                f"{m['past_incident_id']}  [{m['past_severity']}]  "
                f"recovery={m['past_recovery_time_min']}min"
            )
            print(f"       title : {m['past_title']}")
            print(f"       cause : {m['past_root_cause'][:80]}...")
        print()
    print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Historical Incident Matcher (B7)"
    )
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help=f"Top N matches per incident (default {DEFAULT_TOP_N})")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA HISTORICAL INCIDENT MATCHER (B7)")
    print("=" * 78)
    print(f" Causal graph input : {CAUSAL_GRAPH_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Past incidents dir : {PAST_INCIDENTS_DIR.relative_to(PROJECT_ROOT)}")
    print(f" Output             : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f" Top N              : {args.top}")
    print("=" * 78)

    report = build_match_report(args.top)
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        s = report["summary"]
        print(
            f"[INFO] Corpus={report['corpus_size']}  "
            f"incidents={s['current_incident_count']}  "
            f"with_matches={s['incidents_with_matches']}  "
            f"highest={s['highest_match_score']:.4f}"
        )

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
