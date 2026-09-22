#!/usr/bin/env python3
"""
Netwroxia — Phase E8: MLOps Section

Combined view of model registry (D4), drift detector (D5), and
performance tracker (D6). Reads through E1's artifact loader.

Usage:
    from dashboard.sections import mlops
    mlops.render()
    mlops.render(artifacts={...})
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_PROJECT_ROOT = _THIS.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dashboard.utils import artifact_loader as al  # noqa: E402


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _get(artifacts: Dict[str, Any], name: str) -> Dict[str, Any]:
    data = artifacts.get(name)
    if not isinstance(data, dict):
        return {}
    if data.get("_missing") or data.get("_error"):
        return {}
    return data


def _fmt(value: Any, decimals: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def _drift_emoji(status: str) -> str:
    return {
        "STABLE": "🟢",
        "WATCH": "🟡",
        "DRIFTED": "🔴",
        "no_training_data": "⚪",
        "ok": "🟢",
    }.get((status or "").lower().capitalize()
          if status not in ("ok", "no_training_data") else status, "⚪")


def _drift_emoji_simple(status: str) -> str:
    s = (status or "").upper()
    if s == "STABLE":
        return "🟢"
    if s == "WATCH":
        return "🟡"
    if s == "DRIFTED":
        return "🔴"
    if s.lower() == "ok":
        return "🟢"
    return "⚪"


# ── PANEL: SUMMARY ──────────────────────────────────────────────────────────
def render_summary(artifacts: Dict[str, Any]) -> None:
    reg = _get(artifacts, "registry")
    drift = _get(artifacts, "drift")
    perf = _get(artifacts, "performance")

    total = reg.get("total_models") if reg else None
    cu = reg.get("currently_used", {}) if reg else {}
    xgb = (cu.get("xgboost_slot") or "-")
    lstm = (cu.get("lstm_slot") or "-")

    drift_status = drift.get("status") if drift else None
    drift_score = drift.get("overall", {}).get("drift_score") if drift else None

    prec = perf.get("current_snapshot", {}).get("metrics", {}).get("precision") \
        if perf else None
    rec = perf.get("current_snapshot", {}).get("metrics", {}).get("recall") \
        if perf else None
    f1 = perf.get("current_snapshot", {}).get("metrics", {}).get("f1") \
        if perf else None

    cols = st.columns(6)
    cols[0].metric("Total models", total if total is not None else "n/a")
    cols[1].metric("XGB slot", xgb[:24] if xgb != "-" else "-")
    cols[2].metric("LSTM slot", lstm[:24] if lstm != "-" else "-")
    emoji = _drift_emoji_simple(drift_status)
    cols[3].metric("Drift", f"{emoji} {drift_status or 'n/a'}")
    cols[4].metric("Precision / Recall",
                   f"{_fmt(prec, 3)} / {_fmt(rec, 3)}")
    cols[5].metric("F1", _fmt(f1, 3))


# ── PANEL: MODEL REGISTRY ───────────────────────────────────────────────────
def render_registry(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Model Registry")
    reg = _get(artifacts, "registry")
    if not reg:
        st.info("Registry artifact not available. "
                "Run: `python3 mlops/model_registry.py report`")
        return

    by_type = reg.get("by_type", {})
    if not by_type:
        st.caption("No models tracked.")
        return

    for mtype, summary in sorted(by_type.items()):
        count = summary.get("count", 0)
        latest = summary.get("latest_file") or "-"
        modified = summary.get("latest_modified_at") or "?"
        with st.expander(
            f"📦 {mtype}  ·  count={count}",
            expanded=False,
        ):
            st.markdown(
                f"**Latest:** `{latest}`  ·  "
                f"modified: `{modified}`"
            )
            models = [m for m in reg.get("models", [])
                      if m.get("type") == mtype]
            if models:
                rows = []
                for m in models:
                    metrics = m.get("metrics") or {}
                    rows.append({
                        "File": m.get("filename", "?")[:44],
                        "Size (B)": m.get("size_bytes", 0),
                        "Age (h)": _fmt(m.get("age_hours"), 2),
                        "Latest": "yes" if m.get("is_latest_of_type") else "",
                        "Precision": _fmt(metrics.get("precision"), 3),
                        "Recall": _fmt(metrics.get("recall"), 3),
                        "F1": _fmt(metrics.get("f1"), 3),
                    })
                try:
                    import pandas as pd  # noqa
                    st.dataframe(rows, use_container_width=True,
                                 hide_index=True)
                except Exception:
                    for r in rows:
                        st.markdown(f"- `{r['File']}` — F1={r['F1']}")


# ── PANEL: DRIFT ────────────────────────────────────────────────────────────
def render_drift(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Drift Detection")
    drift = _get(artifacts, "drift")
    if not drift:
        st.info("Drift artifact not available. "
                "Run: `python3 mlops/drift_detector.py report`")
        return

    if drift.get("status") != "ok":
        st.warning(
            f"Drift status: `{drift.get('status')}`  ·  "
            f"reason: {drift.get('reason', 'n/a')}"
        )
        return

    overall = drift.get("overall", {})
    emoji = _drift_emoji_simple(overall.get("status"))
    st.markdown(
        f"**Overall:** {emoji} `{overall.get('status')}`  ·  "
        f"score=`{_fmt(overall.get('drift_score'), 4)}`  ·  "
        f"threshold=`{drift.get('threshold')}σ`  ·  "
        f"training samples=`{drift.get('training_samples')}`"
    )

    routers = drift.get("routers", [])
    if not routers:
        st.caption("No routers evaluated.")
        return

    for r in routers:
        status = r.get("status", "?")
        emoji = _drift_emoji_simple(status)
        drifted = r.get("drifted_feature_count", 0)
        with st.expander(
            f"{emoji} {r.get('router')}  ·  "
            f"score={_fmt(r.get('drift_score'), 4)}  ·  "
            f"drifted={drifted}",
            expanded=False,
        ):
            feats = r.get("features", [])
            rows = []
            for f in feats:
                if f.get("drifted") or abs(f.get("z_score") or 0) > 1.5:
                    rows.append({
                        "Feature": f.get("feature", "?"),
                        "Train μ": _fmt(f.get("train_mean"), 4),
                        "Train σ": _fmt(f.get("train_std"), 4),
                        "Live": _fmt(f.get("live_value"), 4),
                        "z": _fmt(f.get("z_score"), 3),
                        "Drifted": "yes" if f.get("drifted") else "",
                    })
            if rows:
                try:
                    import pandas as pd  # noqa
                    st.dataframe(rows, use_container_width=True,
                                 hide_index=True)
                except Exception:
                    for row in rows:
                        st.markdown(f"- `{row['Feature']}` z={row['z']}")
            else:
                st.caption("No notable drift features for this router.")


# ── PANEL: PERFORMANCE ──────────────────────────────────────────────────────
def render_performance(artifacts: Dict[str, Any]) -> None:
    st.markdown("#### Prediction Performance")
    perf = _get(artifacts, "performance")
    if not perf:
        st.info("Performance artifact not available. "
                "Run: `python3 mlops/performance_tracker.py report`")
        return

    if perf.get("status") != "ok":
        st.warning(f"Status: `{perf.get('status')}`  ·  "
                   f"{perf.get('reason', '')}")
        return

    snap = perf.get("current_snapshot", {})
    m = snap.get("metrics", {})

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Precision", _fmt(m.get("precision"), 3))
    c2.metric("Recall", _fmt(m.get("recall"), 3))
    c3.metric("F1", _fmt(m.get("f1"), 3))
    c4.metric("Specificity", _fmt(m.get("specificity"), 3))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("TP", m.get("tp", 0))
    c6.metric("FP", m.get("fp", 0))
    c7.metric("FN", m.get("fn", 0))
    c8.metric("TN", m.get("tn", 0))

    st.caption(
        f"Prediction ts: `{snap.get('prediction_timestamp')}`  ·  "
        f"tolerance: ±{perf.get('tolerance_minutes')} min  ·  "
        f"predictions: {snap.get('total_predictions')}"
    )

    per_router = snap.get("per_router", {})
    if per_router:
        rows = []
        for router, r in per_router.items():
            rows.append({
                "Router": router,
                "Says fault": "yes" if r.get("says_fault") else "",
                "Match": "yes" if r.get("incident_matched") else "",
                "Class": r.get("classification", "?"),
                "Alert": r.get("combined_alert", "?"),
                "Prob": _fmt(r.get("fault_probability"), 4),
            })
        try:
            import pandas as pd  # noqa
            st.dataframe(rows, use_container_width=True, hide_index=True)
        except Exception:
            for row in rows:
                st.markdown(f"- `{row['Router']}` — {row['Class']}")

    # History summary
    hs = perf.get("history_summary", {})
    if perf.get("history_available"):
        st.caption(
            f"History: {hs.get('entries', 0)} entries  ·  "
            f"mean F1=`{_fmt(hs.get('mean_f1'), 4)}`  ·  "
            f"trend=`{hs.get('trend_label')}`"
        )


# ── PANEL: RAW ──────────────────────────────────────────────────────────────
def render_raw(artifacts: Dict[str, Any]) -> None:
    with st.expander("🔍 Raw MLOps artifacts", expanded=False):
        for key in ("registry", "drift", "performance"):
            data = _get(artifacts, key)
            if not data:
                st.caption(f"`{key}` not available.")
                continue
            st.markdown(f"**{key}**")
            st.json({k: v for k, v in list(data.items())[:8]})


# ── PUBLIC ENTRYPOINT ───────────────────────────────────────────────────────
def render(artifacts: Optional[Dict[str, Any]] = None) -> None:
    if artifacts is None:
        artifacts = al.load_all()

    st.subheader("🧪 MLOps Monitoring")
    render_summary(artifacts)
    st.markdown("---")
    render_registry(artifacts)
    st.markdown("---")
    render_drift(artifacts)
    st.markdown("---")
    render_performance(artifacts)
    st.markdown("---")
    render_raw(artifacts)
