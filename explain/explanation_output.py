#!/usr/bin/env python3
"""
Netwroxia — Phase B10: Explanation Output (Phase B final artifact)

Converts SHAP values (B9) into plain-English explanations per router.
Cross-checks SHAP against LSTM forecast and prediction status. Attaches
explanations to RCA incidents where relevant.

Inputs:
  - explain/latest_shap.json                 (B9)
  - ml/inference/latest_prediction.json      (Stage 3)
  - rca/latest_rca.json                      (B8, optional)

Output: explain/latest_explanation.json

Consumed by: Stage 4 copilot (LLM grounding), Phase E dashboard.

CLI:
    python3 explain/explanation_output.py
    python3 explain/explanation_output.py --show
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

SHAP_PATH = PROJECT_ROOT / "explain" / "latest_shap.json"
PREDICTION_PATH = PROJECT_ROOT / "ml" / "inference" / "latest_prediction.json"
RCA_PATH = PROJECT_ROOT / "rca" / "latest_rca.json"
OUTPUT_PATH = PROJECT_ROOT / "explain" / "latest_explanation.json"

TOP_K = 3
DISAGREEMENT_HIGH = 0.5   # SHAP prob and LSTM future prob differ by more than this


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


def humanize_feature(name: str) -> str:
    """Map raw feature name to a friendly label."""
    labels = {
        "average_response_ms": "average RTT latency",
        "percent_packet_loss": "packet loss percentage",
        "count": "OSPF neighbor count",
        "state_ok": "BGP peer state",
        "cpu_percent": "container CPU usage",
        "mem_percent": "container memory usage",
        "router_HO-Chennai": "the HO-Chennai router baseline",
        "router_ZO-Bengaluru": "the ZO-Bengaluru router baseline",
        "router_BR-Koramangala": "the BR-Koramangala router baseline",
        "router_BR-Whitefield": "the BR-Whitefield router baseline",
    }
    return labels.get(name, name)


def describe_contribution(c: Dict[str, Any]) -> str:
    """Turn one contribution into a short English phrase."""
    feat = humanize_feature(c["feature"])
    sv = c["shap"]
    direction = c["direction"]
    val = c["value"]
    sign = "+" if sv > 0 else ""
    if direction == "increases":
        verb = "raised"
    elif direction == "reduces":
        verb = "lowered"
    else:
        verb = "did not affect"
    return f"{feat} (value={val}) {verb} the fault score by {sign}{sv:.3f}"


def build_explanation_text(
    router: str,
    prob: float,
    top: List[Dict[str, Any]],
    all_contribs: List[Dict[str, Any]],
) -> str:
    """Compose a 2-3 sentence explanation from SHAP top-K."""
    if not top:
        return f"{router}: no SHAP contributions available."

    prob_pct = f"{prob * 100:.2f}%"

    driver_sentences = []
    for c in top[:2]:
        driver_sentences.append(describe_contribution(c))

    # Check if any strong negative contributors
    strong_reducers = [
        c for c in all_contribs
        if c["direction"] == "reduces" and abs(c["shap"]) > 0.5
    ]
    strong_increasers = [
        c for c in all_contribs
        if c["direction"] == "increases" and abs(c["shap"]) > 0.5
    ]

    intro = f"{router}: XGBoost fault probability is {prob_pct}."
    drivers = "Main drivers — " + "; ".join(driver_sentences) + "."

    context_parts = []
    if strong_reducers:
        ctx = ", ".join(humanize_feature(c["feature"]) for c in strong_reducers[:2])
        context_parts.append(f"{ctx} strongly reduced the risk estimate")
    if strong_increasers:
        ctx = ", ".join(humanize_feature(c["feature"]) for c in strong_increasers[:2])
        context_parts.append(f"{ctx} raised the risk estimate")

    context = ""
    if context_parts:
        context = "Context: " + "; ".join(context_parts) + "."

    return " ".join([intro, drivers, context]).strip()


def compare_with_lstm(shap_prob: float, lstm_future_prob: Optional[float]) -> Dict[str, Any]:
    """Cross-check SHAP-based current probability against LSTM future probability."""
    if lstm_future_prob is None:
        return {
            "compared": False,
            "reason": "no LSTM forecast available",
            "agreement": None,
            "gap": None,
        }
    gap = abs(shap_prob - lstm_future_prob)
    if gap < 0.15:
        agreement = "strong"
    elif gap < 0.35:
        agreement = "moderate"
    elif gap < DISAGREEMENT_HIGH:
        agreement = "weak"
    else:
        agreement = "conflicting"
    return {
        "compared": True,
        "shap_probability": round(shap_prob, 4),
        "lstm_future_probability": round(lstm_future_prob, 4),
        "gap": round(gap, 4),
        "agreement": agreement,
    }


def compare_with_prediction_status(shap_prob: float, xgb_status: Optional[str]) -> Dict[str, Any]:
    """Check if SHAP prob aligns with prediction.json's xgb status."""
    if xgb_status is None:
        return {"compared": False}
    if shap_prob >= 0.5 and xgb_status != "HEALTHY":
        alignment = "aligned"
    elif shap_prob < 0.5 and xgb_status == "HEALTHY":
        alignment = "aligned"
    else:
        alignment = "misaligned"
    return {
        "compared": True,
        "shap_probability": round(shap_prob, 4),
        "prediction_status": xgb_status,
        "alignment": alignment,
    }


# ── ASSEMBLY ────────────────────────────────────────────────────────────────
def build_router_explanation(
    router_entry: Dict[str, Any],
    prediction_entry: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    router = router_entry["router"]
    prob = float(router_entry["fault_probability"])
    top = router_entry.get("top_contributions", [])[:TOP_K]
    all_c = router_entry.get("all_contributions", [])

    text = build_explanation_text(router, prob, top, all_c)

    lstm_future = None
    xgb_status = None
    if prediction_entry:
        lstm = prediction_entry.get("lstm_forecast", {}) or {}
        lstm_future = lstm.get("future_fault_probability")
        xgb_status = (prediction_entry.get("xgboost", {}) or {}).get("status")

    return {
        "router": router,
        "fault_probability": round(prob, 4),
        "explanation_text": text,
        "top_contributors": top,
        "lstm_cross_check": compare_with_lstm(prob, lstm_future),
        "prediction_cross_check": compare_with_prediction_status(prob, xgb_status),
    }


def attach_to_rca_incidents(
    router_explanations: Dict[str, Dict[str, Any]],
    rca: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach explanation text to each RCA incident's root router."""
    if not rca:
        return []
    out: List[Dict[str, Any]] = []
    for inc in rca.get("incidents", []):
        root = inc.get("summary", {}).get("root")
        if not root:
            continue
        expl = router_explanations.get(root)
        out.append({
            "incident_id": inc.get("incident_id"),
            "root": root,
            "severity": inc.get("summary", {}).get("severity"),
            "confidence": inc.get("summary", {}).get("confidence"),
            "explanation": expl["explanation_text"] if expl else "",
            "top_contributors": expl["top_contributors"] if expl else [],
        })
    return out


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_explanation_report() -> Dict[str, Any]:
    shap_data = load_json(SHAP_PATH, required=True)
    prediction = load_json(PREDICTION_PATH) or {}
    rca = load_json(RCA_PATH) or {}

    prediction_by_router: Dict[str, Dict[str, Any]] = {
        p["router"]: p for p in prediction.get("predictions", []) if "router" in p
    }

    router_explanations: Dict[str, Dict[str, Any]] = {}
    for entry in shap_data.get("routers", []):
        rname = entry["router"]
        built = build_router_explanation(entry, prediction_by_router.get(rname))
        router_explanations[rname] = built

    incident_explanations = attach_to_rca_incidents(router_explanations, rca)

    # Overall narrative
    highest = max(
        (r["fault_probability"] for r in router_explanations.values()),
        default=0.0,
    )
    if highest < 0.5:
        overall = (
            f"All {len(router_explanations)} routers are below the fault threshold. "
            f"Highest fault probability: {highest * 100:.2f}%."
        )
    else:
        hot = [r for r in router_explanations.values() if r["fault_probability"] >= 0.5]
        names = ", ".join(h["router"] for h in hot)
        overall = (
            f"{len(hot)} router(s) above fault threshold: {names}. "
            f"Highest: {highest * 100:.2f}%."
        )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "shap": str(SHAP_PATH.relative_to(PROJECT_ROOT)),
            "prediction": str(PREDICTION_PATH.relative_to(PROJECT_ROOT)),
            "rca": str(RCA_PATH.relative_to(PROJECT_ROOT)),
        },
        "shap_method": shap_data.get("method", "unknown"),
        "summary": {
            "router_count": len(router_explanations),
            "incident_count": len(incident_explanations),
            "highest_fault_probability": round(highest, 4),
            "has_conflicting_signals": any(
                r["lstm_cross_check"].get("agreement") == "conflicting"
                for r in router_explanations.values()
            ),
        },
        "overall_narrative": overall,
        "router_explanations": router_explanations,
        "incident_explanations": incident_explanations,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print("─" * 78)
    print(f" Method             : {report['shap_method']}")
    print(f" Routers            : {s['router_count']}")
    print(f" Incidents enriched : {s['incident_count']}")
    print(f" Highest prob       : {s['highest_fault_probability']:.4f}")
    print(f" Conflicts          : {s['has_conflicting_signals']}")
    print("─" * 78)
    print(f" {report['overall_narrative']}")
    print("─" * 78)

    for rname, r in report["router_explanations"].items():
        print(f" {rname}")
        print(f"   {r['explanation_text']}")
        cross = r["lstm_cross_check"]
        if cross.get("compared"):
            print(f"   LSTM cross-check: agreement={cross['agreement']} "
                  f"gap={cross['gap']:.4f}")
        pred = r["prediction_cross_check"]
        if pred.get("compared"):
            print(f"   Prediction align: {pred['alignment']}")
        print()

    if report["incident_explanations"]:
        print("─" * 78)
        print(" INCIDENT-LEVEL EXPLANATIONS")
        for inc in report["incident_explanations"]:
            print(f" [{inc['severity']}] {inc['incident_id']}  root={inc['root']}")
            print(f"   {inc['explanation']}")
        print("─" * 78)


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Explanation Output (B10)"
    )
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()

    print("=" * 78)
    print(" NETWROXIA EXPLANATION OUTPUT (B10)")
    print("=" * 78)
    print(f" Output : {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 78)

    report = build_explanation_report()
    save_json(OUTPUT_PATH, report)

    if args.show:
        print_summary(report)
    else:
        s = report["summary"]
        print(f"[INFO] routers={s['router_count']}  "
              f"incidents={s['incident_count']}  "
              f"highest={s['highest_fault_probability']:.4f}  "
              f"conflicts={s['has_conflicting_signals']}")

    print(f"[INFO] Report written -> {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
