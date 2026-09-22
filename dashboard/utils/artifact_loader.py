#!/usr/bin/env python3
"""
Netwroxia — Phase E1: Artifact Loader

Centralized, cached, graceful reader for every JSON artifact produced
by Phase A/B/C/D and Stages 3-4. Every dashboard tab reads through here.

Guarantees:
  - Never raises on missing/corrupt files
  - Missing file  -> {"_missing": True, "_path": "..."}
  - Corrupt JSON  -> {"_error": "message", "_path": "..."}
  - Cached by file mtime — unchanged files aren't re-parsed

Env override: NETWROXIA_PROJECT_ROOT (for tests)

No Streamlit dependency. Pure stdlib.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ── PROJECT ROOT ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(os.environ.get(
    "NETWROXIA_PROJECT_ROOT",
    str(Path(__file__).resolve().parent.parent.parent),
))


# ── ARTIFACT CATALOG ────────────────────────────────────────────────────────
def _rel(p: str) -> Path:
    return PROJECT_ROOT / p

# Central catalog: name -> path
ARTIFACTS: Dict[str, Path] = {
    # Stage 3-4
    "prediction":         _rel("ml/inference/latest_prediction.json"),
    "copilot_response":   _rel("copilot/llm/latest_copilot_response.json"),

    # Phase A — Beacons
    "beacon_state":       _rel("beacons/latest_beacon_state.json"),
    "beacon_health":      _rel("beacons/latest_beacon_health.json"),
    "baselines":          _rel("beacons/latest_baselines.json"),

    # Phase A — Fault library
    "active_faults":      _rel("fault_sim/active_faults.json"),

    # Phase B — Health / Impact
    "health":             _rel("analytics/latest_health.json"),
    "impact":             _rel("impact/latest_impact.json"),
    "business_impact":    _rel("impact/latest_business_impact.json"),
    "service_map":        _rel("impact/service_map.json"),

    # Phase B — RCA
    "correlation":        _rel("rca/latest_correlation.json"),
    "causal_graph":       _rel("rca/latest_causal_graph.json"),
    "historical_match":   _rel("rca/latest_historical_match.json"),
    "rca":                _rel("rca/latest_rca.json"),

    # Phase B — Explainability
    "shap":               _rel("explain/latest_shap.json"),
    "explanation":        _rel("explain/latest_explanation.json"),

    # Phase D — Analytics
    "kpis":               _rel("analytics/latest_kpis.json"),
    "sla":                _rel("analytics/latest_sla.json"),
    "trends":             _rel("analytics/latest_trends.json"),

    # Phase D — MLOps
    "registry":           _rel("mlops/latest_registry.json"),
    "drift":              _rel("mlops/latest_drift.json"),
    "performance":        _rel("mlops/latest_performance.json"),

    # Phase D — Reports
    "daily_report":       _rel("reports/daily/latest.json"),
    "weekly_report":      _rel("reports/weekly/latest.json"),

    # Phase C — Approvals
    "pending_approvals":  _rel("remediation/engine/pending_approval.json"),
}


# ── CACHE ───────────────────────────────────────────────────────────────────
# mtime-based cache: {name: (mtime, data)}
_cache: Dict[str, Tuple[float, Any]] = {}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _safe_relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _missing(name: str) -> Dict[str, Any]:
    return {
        "_missing": True,
        "_path": _safe_relpath(ARTIFACTS.get(name, Path(name))),
    }


def _error(name: str, msg: str) -> Dict[str, Any]:
    return {
        "_error": msg,
        "_path": _safe_relpath(ARTIFACTS.get(name, Path(name))),
    }


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def load(name: str) -> Dict[str, Any]:
    """
    Load an artifact by name. Cached by mtime.

    Returns the parsed dict, OR a sentinel dict with `_missing` or
    `_error` set. Never raises.
    """
    path = ARTIFACTS.get(name)
    if path is None:
        return {
            "_error": f"unknown artifact: {name}",
            "_path": name,
        }

    if not path.exists():
        _cache.pop(name, None)
        return _missing(name)

    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        return _error(name, f"stat failed: {e}")

    cached = _cache.get(name)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return _error(name, f"invalid JSON: {e}")
    except OSError as e:
        return _error(name, f"read failed: {e}")

    _cache[name] = (mtime, data)
    return data


def load_all() -> Dict[str, Dict[str, Any]]:
    """Load every catalogued artifact. Returns {name: data}."""
    return {name: load(name) for name in ARTIFACTS}


def is_available(name: str) -> bool:
    """True if the artifact exists and parsed cleanly."""
    data = load(name)
    return not (data.get("_missing") or data.get("_error"))


def invalidate(name: Optional[str] = None) -> None:
    """Drop cache for one artifact, or all if name is None."""
    if name is None:
        _cache.clear()
    else:
        _cache.pop(name, None)


def list_artifacts() -> Dict[str, Dict[str, Any]]:
    """
    Diagnostic view: name -> {path, exists, size_bytes, mtime_iso}.
    """
    from datetime import datetime, timezone
    out: Dict[str, Dict[str, Any]] = {}
    for name, path in ARTIFACTS.items():
        entry: Dict[str, Any] = {"path": _safe_relpath(path)}
        if path.exists():
            try:
                st = path.stat()
                entry["exists"] = True
                entry["size_bytes"] = st.st_size
                entry["mtime_iso"] = datetime.fromtimestamp(
                    st.st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                entry["exists"] = False
        else:
            entry["exists"] = False
        out[name] = entry
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────
def _print_status() -> None:
    print(f"PROJECT_ROOT: {PROJECT_ROOT}")
    print(f"Artifacts   : {len(ARTIFACTS)}")
    print("─" * 78)
    for name, info in sorted(list_artifacts().items()):
        mark = "OK " if info["exists"] else "-- "
        size = info.get("size_bytes", 0)
        print(f"  [{mark}] {name:20s}  {size:>8d}B  {info['path']}")


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "load":
        if len(sys.argv) < 3:
            print("usage: artifact_loader.py load <name>")
            return
        data = load(sys.argv[2])
        print(json.dumps(data, indent=2, default=str)[:2000])
        return
    _print_status()


if __name__ == "__main__":
    main()
