#!/usr/bin/env python3
"""
Netwroxia — Phase D2: SLA Compliance Tracker

For each banking service and branch, computes actual uptime over a
configurable window and checks compliance against the SLA target
defined in impact/service_map.json (B2).

Data sources:
  impact/service_map.json                  (B2) SLA targets + host routers
  incidents/incidents.db                   (C2) incident history
  analytics/latest_health.json             (B1) current health snapshot

Output: analytics/latest_sla.json

Uptime formula (severity-weighted):
  downtime = sum over incidents affecting host routers of
             (incident_duration_seconds * severity_weight)
  actual_uptime_pct = (window_seconds - downtime) / window_seconds * 100

Severity weights (rationale: not all incidents are full outages):
  CRITICAL  1.0   total outage
  HIGH      0.7   partial
  MEDIUM    0.4   degraded
  LOW       0.1   negligible

CLI:
    python3 analytics/sla_tracker.py report
    python3 analytics/sla_tracker.py report --show
    python3 analytics/sla_tracker.py report --window 7
    python3 analytics/sla_tracker.py report --no-current
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── PATH SETUP ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from incidents import incident_store as store

# ── CONFIG ──────────────────────────────────────────────────────────────────
SERVICE_MAP_PATH = PROJECT_ROOT / "impact" / "service_map.json"
HEALTH_PATH = PROJECT_ROOT / "analytics" / "latest_health.json"
OUTPUT_PATH = PROJECT_ROOT / "analytics" / "latest_sla.json"

DEFAULT_WINDOW_DAYS = 30

SEVERITY_WEIGHT = {
    "CRITICAL": 1.0,
    "HIGH": 0.7,
    "MEDIUM": 0.4,
    "LOW": 0.1,
}

IMPAIRED_STATUSES = {"CRITICAL", "DOWN"}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _load_json(path: Path, required: bool = True) -> Optional[Dict[str, Any]]:
    if not path.exists():
        if required:
            print(f"[FATAL] missing required file: {path}")
            sys.exit(1)
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[FATAL] malformed JSON in {path}: {e}")
        sys.exit(1)


def _extract_chain(incident: Dict[str, Any]) -> List[str]:
    """
    Pull propagation chain from an incident's rca_snapshot.
    Falls back to [root_router] if the snapshot is missing.
    """
    root = incident.get("root_router") or ""
    raw = incident.get("rca_snapshot")
    if raw:
        try:
            snap = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            snap = None
        if isinstance(snap, dict):
            summary = snap.get("summary", {})
            chain = summary.get("chain")
            if isinstance(chain, list) and chain:
                return list(chain)
    return [root] if root else []


def _service_host_routers(service_map: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Invert routers[].services_hosted -> {service_id: [routers]}.
    """
    out: Dict[str, List[str]] = {}
    for router_name, cfg in (service_map.get("routers") or {}).items():
        for sid in (cfg.get("services_hosted") or []):
            out.setdefault(sid, []).append(router_name)
    return out


# ── SLA COMPUTATION ─────────────────────────────────────────────────────────
def _incident_downtime_contribution(
    incident: Dict[str, Any],
    host_routers: Set[str],
    window_start: datetime,
    window_end: datetime,
) -> Optional[Dict[str, Any]]:
    """
    Return the weighted downtime contribution of one incident for a set
    of host routers. Returns None if the incident does not affect them
    or is outside the window.

    Result:
      {"seconds": float, "severity": str, "weight": float,
       "incident_id": str, "root": str}
    """
    chain = set(_extract_chain(incident))
    if not (chain & host_routers):
        return None

    created = _parse_iso(incident.get("created_at"))
    if created is None:
        return None

    resolved = _parse_iso(incident.get("resolved_at"))
    end = resolved if resolved is not None else window_end

    # Clip to window
    start_in_window = max(created, window_start)
    end_in_window = min(end, window_end)
    duration = (end_in_window - start_in_window).total_seconds()
    if duration <= 0:
        return None

    severity = (incident.get("severity") or "LOW").upper()
    weight = SEVERITY_WEIGHT.get(severity, 0.1)

    return {
        "seconds": round(duration, 2),
        "severity": severity,
        "weight": weight,
        "incident_id": incident.get("incident_id", ""),
        "root": incident.get("root_router", ""),
    }


def compute_service_sla(
    service_id: str,
    service_cfg: Dict[str, Any],
    host_routers: List[str],
    incidents: List[Dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    current_health: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    window_seconds = (window_end - window_start).total_seconds()
    sla_target = float(service_cfg.get("sla_uptime_pct", 99.0))
    host_set = set(host_routers)

    weighted_downtime = 0.0
    contributing: List[Dict[str, Any]] = []

    for inc in incidents:
        contrib = _incident_downtime_contribution(
            inc, host_set, window_start, window_end
        )
        if contrib is None:
            continue
        weighted_downtime += contrib["seconds"] * contrib["weight"]
        contributing.append(contrib)

    # Cap downtime at window size (safety against overlapping accounting)
    weighted_downtime = min(weighted_downtime, window_seconds)

    if window_seconds > 0:
        actual_uptime = ((window_seconds - weighted_downtime) / window_seconds) * 100.0
    else:
        actual_uptime = 100.0

    actual_uptime = round(actual_uptime, 4)
    compliant = actual_uptime >= sla_target

    # Current impairment: any host router CRITICAL/DOWN right now?
    currently_impaired = False
    impaired_hosts: List[str] = []
    if current_health:
        for r in host_routers:
            h = current_health.get(r)
            if h and h.get("status") in IMPAIRED_STATUSES:
                currently_impaired = True
                impaired_hosts.append(r)

    return {
        "service_id": service_id,
        "name": service_cfg.get("name", service_id),
        "criticality": service_cfg.get("criticality", "medium"),
        "rbi_mandated": bool(service_cfg.get("rbi_mandated", False)),
        "revenue_per_min_inr": int(service_cfg.get("revenue_per_min_inr", 0)),
        "sla_target_pct": sla_target,
        "actual_uptime_pct": actual_uptime,
        "compliant": compliant,
        "margin_pct": round(actual_uptime - sla_target, 4),
        "downtime_seconds": round(weighted_downtime, 2),
        "incident_count": len(contributing),
        "contributing_incidents": contributing,
        "host_routers": host_routers,
        "currently_impaired": currently_impaired,
        "impaired_hosts": impaired_hosts,
    }


def compute_branch_sla(
    router_name: str,
    router_cfg: Dict[str, Any],
    service_results: Dict[str, Dict[str, Any]],
    window_seconds: float,
) -> Dict[str, Any]:
    """
    Aggregate SLA across all services hosted at a router.
    Branch uptime = 1 - (sum of weighted downtime across its services) / window
    Capped at window_seconds so no negative uptime.
    """
    hosted = router_cfg.get("services_hosted") or []
    if not hosted:
        return {
            "router": router_name,
            "role": router_cfg.get("role", "unknown"),
            "hosted_services": [],
            "services_count": 0,
            "aggregate_downtime_seconds": 0.0,
            "avg_sla_target_pct": 0.0,
            "actual_uptime_pct": 100.0,
            "compliant": True,
            "at_risk_services": [],
        }

    total_downtime = 0.0
    targets: List[float] = []
    at_risk: List[str] = []

    for sid in hosted:
        svc = service_results.get(sid)
        if svc is None:
            continue
        total_downtime += svc["downtime_seconds"]
        targets.append(svc["sla_target_pct"])
        if not svc["compliant"] or svc["currently_impaired"]:
            at_risk.append(sid)

    total_downtime = min(total_downtime, window_seconds)
    if window_seconds > 0:
        actual_uptime = ((window_seconds - total_downtime) / window_seconds) * 100.0
    else:
        actual_uptime = 100.0
    actual_uptime = round(actual_uptime, 4)

    avg_target = round(sum(targets) / len(targets), 4) if targets else 0.0

    return {
        "router": router_name,
        "role": router_cfg.get("role", "unknown"),
        "hosted_services": sorted(hosted),
        "services_count": len(hosted),
        "aggregate_downtime_seconds": round(total_downtime, 2),
        "avg_sla_target_pct": avg_target,
        "actual_uptime_pct": actual_uptime,
        "compliant": actual_uptime >= avg_target,
        "at_risk_services": sorted(at_risk),
    }


def _safe_relpath(path: Path) -> str:
    """Return path relative to PROJECT_ROOT if possible, else absolute str."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# ── ORCHESTRATION ───────────────────────────────────────────────────────────
def build_sla_report(
    window_days: int = DEFAULT_WINDOW_DAYS,
    skip_current: bool = False,
) -> Dict[str, Any]:
    service_map = _load_json(SERVICE_MAP_PATH, required=True)
    current_health = None if skip_current else _load_json(HEALTH_PATH, required=False)

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=window_days)
    window_seconds = (window_end - window_start).total_seconds()

    incidents = store.list_incidents(limit=5000)

    services = service_map.get("services") or {}
    routers = service_map.get("routers") or {}
    service_hosts = _service_host_routers(service_map)

    health_routers = (current_health or {}).get("routers", {})

    service_results: Dict[str, Dict[str, Any]] = {}
    for sid, cfg in services.items():
        hosts = service_hosts.get(sid, [])
        service_results[sid] = compute_service_sla(
            service_id=sid,
            service_cfg=cfg,
            host_routers=hosts,
            incidents=incidents,
            window_start=window_start,
            window_end=window_end,
            current_health=health_routers,
        )

    branch_results: Dict[str, Dict[str, Any]] = {}
    for rname, rcfg in routers.items():
        branch_results[rname] = compute_branch_sla(
            router_name=rname,
            router_cfg=rcfg,
            service_results=service_results,
            window_seconds=window_seconds,
        )

    # Aggregate
    total_services = len(service_results)
    compliant_services = sum(
        1 for s in service_results.values() if s["compliant"]
    )
    at_risk_services = [
        s["service_id"] for s in service_results.values() if not s["compliant"]
    ]
    currently_impaired_services = [
        s["service_id"] for s in service_results.values() if s["currently_impaired"]
    ]

    total_branches = len(branch_results)
    compliant_branches = sum(
        1 for b in branch_results.values() if b["compliant"]
    )
    at_risk_branches = [
        b["router"] for b in branch_results.values() if not b["compliant"]
    ]

    return {
        "timestamp": window_end.isoformat(),
        "window_days": window_days,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "sources": {
            "service_map": _safe_relpath(SERVICE_MAP_PATH),
            "health": _safe_relpath(HEALTH_PATH),
            "incidents_db": str(store.DB_PATH),
        },
        "overall": {
            "total_services": total_services,
            "compliant_services": compliant_services,
            "at_risk_services": at_risk_services,
            "currently_impaired_services": currently_impaired_services,
            "total_branches": total_branches,
            "compliant_branches": compliant_branches,
            "at_risk_branches": at_risk_branches,
            "compliance_rate": round(
                (compliant_services / total_services) * 100.0, 2
            ) if total_services > 0 else 100.0,
        },
        "services": service_results,
        "branches": branch_results,
    }


# ── DISPLAY ─────────────────────────────────────────────────────────────────
def print_summary(report: Dict[str, Any]) -> None:
    hr = "─" * 78
    ov = report["overall"]
    print(hr)
    print(f" NETWROXIA — SLA COMPLIANCE ({report['window_days']}-day window)")
    print(hr)
    print(f" Services compliant : {ov['compliant_services']}/{ov['total_services']}"
          f"  ({ov['compliance_rate']:.1f}%)")
    print(f" Branches compliant : {ov['compliant_branches']}/{ov['total_branches']}")
    if ov["currently_impaired_services"]:
        print(f" Currently impaired : {', '.join(ov['currently_impaired_services'])}")
    print(hr)

    print(" SERVICES")
    for sid, s in sorted(report["services"].items(),
                         key=lambda kv: (not kv[1]["compliant"],
                                         kv[1]["margin_pct"])):
        status = "OK" if s["compliant"] else "BREACH"
        imp = " [IMPAIRED]" if s["currently_impaired"] else ""
        rbi = "RBI" if s["rbi_mandated"] else "   "
        print(f"   [{rbi}] {sid:22s} "
              f"target={s['sla_target_pct']:5.2f}%  "
              f"actual={s['actual_uptime_pct']:6.2f}%  "
              f"margin={s['margin_pct']:+6.2f}%  "
              f"[{status}]{imp}")

    print(hr)
    print(" BRANCHES")
    for rname, b in sorted(report["branches"].items(),
                           key=lambda kv: (not kv[1]["compliant"],
                                           kv[1]["actual_uptime_pct"])):
        status = "OK" if b["compliant"] else "BREACH"
        print(f"   {rname:18s} "
              f"target={b['avg_sla_target_pct']:5.2f}%  "
              f"actual={b['actual_uptime_pct']:6.2f}%  "
              f"services={b['services_count']:2d}  "
              f"[{status}]")
        if b["at_risk_services"]:
            print(f"      at risk: {', '.join(b['at_risk_services'])}")
    print(hr)


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_report(args) -> int:
    report = build_sla_report(
        window_days=args.window,
        skip_current=args.no_current,
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    try:
        rel = OUTPUT_PATH.relative_to(PROJECT_ROOT)
    except ValueError:
        rel = OUTPUT_PATH
    if args.show:
        print_summary(report)
    else:
        print(f"[OK] SLA report written to {rel}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Netwroxia SLA Tracker (D2)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_rep = sub.add_parser("report", help="Compute and write SLA report")
    p_rep.add_argument("--show", action="store_true")
    p_rep.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS,
                       help="SLA window in days (default 30)")
    p_rep.add_argument("--no-current", action="store_true",
                       help="Skip B1 current health lookup")

    args = parser.parse_args()
    if args.command == "report":
        sys.exit(cmd_report(args))


if __name__ == "__main__":
    main()
