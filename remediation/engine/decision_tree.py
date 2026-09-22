#!/usr/bin/env python3
"""
Netwroxia — Phase C4: Decision Tree

Pure function: given incident properties, choose one of 5 whitelisted
remediation actions. No I/O, no state, no side effects.

Whitelist: no_op | restart_bgp | clear_ospf | reroute_traffic | escalate_to_human

Consumed by C14 executor. Every call must be deterministic — same inputs
always produce the same output.

CLI:
    python3 remediation/engine/decision_tree.py decide \
        --severity CRITICAL --root-role head_office --root-router HO-Chennai
    python3 remediation/engine/decision_tree.py test       # run built-in table tests
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

# ── CONSTANTS ───────────────────────────────────────────────────────────────
VALID_ACTIONS = {
    "no_op",
    "restart_bgp",
    "clear_ospf",
    "reroute_traffic",
    "escalate_to_human",
}

VALID_SEVERITIES = {"NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
VALID_ROLES = {"head_office", "zonal_office", "branch"}

# Fallback chains — progressively safer
FALLBACK_CHAINS = {
    "no_op":            ["no_op", "restart_bgp", "escalate_to_human"],
    "restart_bgp":      ["restart_bgp", "clear_ospf", "escalate_to_human"],
    "clear_ospf":       ["clear_ospf", "restart_bgp", "escalate_to_human"],
    "reroute_traffic":  ["reroute_traffic", "restart_bgp", "escalate_to_human"],
    "escalate_to_human": ["escalate_to_human"],
}

# Human-readable reason per (severity, role) combination
SEVERITY_ROLE_REASON = {
    ("NONE",     "*"):            "no incident; monitoring only",
    ("LOW",      "*"):            "low severity; watching without intervention",
    ("MEDIUM",   "head_office"):  "medium incident at HO; escalating for human review",
    ("MEDIUM",   "zonal_office"): "medium incident at ZO; clearing OSPF is the cheapest safe action",
    ("MEDIUM",   "branch"):       "medium incident at branch; local BGP restart is safe and effective",
    ("HIGH",     "head_office"):  "high incident at HO; escalating — never auto-touch HO",
    ("HIGH",     "zonal_office"): "high incident at ZO; rerouting traffic preserves downstream service",
    ("HIGH",     "branch"):       "high incident at branch; local BGP restart is safe and effective",
    ("CRITICAL", "head_office"):  "critical incident at HO; escalating — never auto-touch HO",
    ("CRITICAL", "zonal_office"): "critical incident at ZO; rerouting traffic preserves downstream service",
    ("CRITICAL", "branch"):       "critical incident at branch; local BGP restart is safe and effective",
}

# Fault-hint override map
FAULT_HINT_MAP = {
    "bgp":          "restart_bgp",
    "ospf":         "clear_ospf",
    "packet_loss":  "reroute_traffic",
    "latency":      "reroute_traffic",
    "congestion":   "reroute_traffic",
    "interface":    "reroute_traffic",
}


# ── HELPERS ─────────────────────────────────────────────────────────────────
def _validate_severity(severity: str) -> str:
    s = (severity or "UNKNOWN").upper()
    if s not in VALID_SEVERITIES:
        s = "UNKNOWN"
    return s


def _validate_role(role: str) -> str:
    r = (role or "unknown").lower()
    if r not in VALID_ROLES:
        r = "unknown"
    return r


def _lookup_default(severity: str, role: str) -> str:
    """
    Apply the severity+role table. Fallback to escalate_to_human for
    unknown role with any non-LOW severity.
    """
    if severity in {"NONE", "LOW"}:
        return "no_op"
    if role == "head_office":
        return "escalate_to_human"
    if role == "zonal_office":
        if severity == "MEDIUM":
            return "clear_ospf"
        return "reroute_traffic"     # HIGH, CRITICAL
    if role == "branch":
        return "restart_bgp"
    # Unknown role
    return "escalate_to_human"


def _lookup_reason(severity: str, role: str) -> str:
    if severity in {"NONE", "LOW"}:
        return SEVERITY_ROLE_REASON[(severity, "*")]
    key = (severity, role)
    if key in SEVERITY_ROLE_REASON:
        return SEVERITY_ROLE_REASON[key]
    return f"{severity} severity with unknown role; defaulting to escalate"


def _compute_confidence(
    fault_hint: Optional[str],
    history_match_score: Optional[float],
    severity: str,
) -> float:
    base = 0.55
    if fault_hint:
        base += 0.15
    if history_match_score is not None and history_match_score >= 0.70:
        base += 0.10
    if severity in {"HIGH", "CRITICAL"}:
        base += 0.05
    return round(min(base, 0.95), 3)


def _fault_hint_match(fault_hint: Optional[str], severity: str) -> Optional[str]:
    """
    Return the action implied by the fault hint, or None if no hint.
    packet_loss/latency/congestion/interface hints only apply at HIGH+;
    bgp/ospf hints apply at MEDIUM+.
    """
    if not fault_hint:
        return None
    hint = fault_hint.lower().strip()

    # Scan for known keywords
    for key, action in FAULT_HINT_MAP.items():
        if key in hint:
            # Gate weaker hints by severity
            if key in {"bgp", "ospf"}:
                if severity in {"MEDIUM", "HIGH", "CRITICAL"}:
                    return action
            else:  # packet_loss, latency, congestion, interface
                if severity in {"HIGH", "CRITICAL"}:
                    return action
    return None


# ── PUBLIC API ──────────────────────────────────────────────────────────────
def decide(
    severity: str,
    root_role: str,
    root_router: str = "",
    fault_hint: Optional[str] = None,
    history_match_score: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Choose a remediation action for an incident.

    Parameters
    ----------
    severity : str             NONE | LOW | MEDIUM | HIGH | CRITICAL
    root_role : str            head_office | zonal_office | branch
    root_router : str          router name (for logging only)
    fault_hint : Optional[str] free-form hint like "bgp", "packet_loss"
    history_match_score : Optional[float]  from B7 (0.0-1.0)

    Returns
    -------
    Dict with keys: action, action_source, confidence, requires_approval,
    target_router, target_role, reason, fallback_chain, inputs.
    """
    sev = _validate_severity(severity)
    role = _validate_role(root_role)

    # Priority 1: fault-hint override
    override = _fault_hint_match(fault_hint, sev)
    if override is not None:
        action = override
        source = "fault_hint_override"
        reason = f"fault_hint='{fault_hint}' implies action '{action}' at {sev} severity"
    else:
        # Priority 2: severity + role table
        action = _lookup_default(sev, role)
        source = "role_severity_table"
        reason = _lookup_reason(sev, role)

    # Safety net: never auto-touch HO for any severity above LOW
    if role == "head_office" and sev not in {"NONE", "LOW"}:
        if action != "escalate_to_human":
            action = "escalate_to_human"
            source = "safety_override_ho"
            reason = (
                f"HO root at {sev} severity always escalates for human review; "
                f"override applied"
            )

    # Safety net: unknown role with any severity above LOW
    if role == "unknown" and sev not in {"NONE", "LOW"}:
        action = "escalate_to_human"
        source = "safety_override_unknown_role"
        reason = f"unknown root role at {sev} severity; escalating"

    confidence = _compute_confidence(fault_hint, history_match_score, sev)
    requires_approval = sev == "CRITICAL"
    fallback = FALLBACK_CHAINS[action]

    return {
        "action": action,
        "action_source": source,
        "confidence": confidence,
        "requires_approval": requires_approval,
        "target_router": root_router or "",
        "target_role": role,
        "reason": reason,
        "fallback_chain": fallback,
        "inputs": {
            "severity": sev,
            "root_role": role,
            "fault_hint": fault_hint,
            "history_match_score": history_match_score,
        },
    }


# ── CLI ─────────────────────────────────────────────────────────────────────
def cmd_decide(args) -> int:
    out = decide(
        severity=args.severity,
        root_role=args.root_role,
        root_router=args.root_router,
        fault_hint=args.fault_hint,
        history_match_score=args.history_match,
    )
    print(json.dumps(out, indent=2))
    return 0


# Built-in table test — same as /tmp/test_c4.py but runnable from CLI
def cmd_test(_args) -> int:
    cases = [
        # (severity, role, hint, expected)
        ("NONE",     "head_office",  None, "no_op"),
        ("LOW",      "branch",       None, "no_op"),
        ("MEDIUM",   "head_office",  None, "escalate_to_human"),
        ("MEDIUM",   "zonal_office", None, "clear_ospf"),
        ("MEDIUM",   "branch",       None, "restart_bgp"),
        ("HIGH",     "head_office",  None, "escalate_to_human"),
        ("HIGH",     "zonal_office", None, "reroute_traffic"),
        ("HIGH",     "branch",       None, "restart_bgp"),
        ("CRITICAL", "head_office",  None, "escalate_to_human"),
        ("CRITICAL", "zonal_office", None, "reroute_traffic"),
        ("CRITICAL", "branch",       None, "restart_bgp"),
        # Fault hints
        ("MEDIUM",   "branch",       "bgp",        "restart_bgp"),
        ("HIGH",     "branch",       "ospf",       "clear_ospf"),
        ("HIGH",     "zonal_office", "packet_loss","reroute_traffic"),
        # Hint gating: packet_loss at MEDIUM should NOT override
        ("MEDIUM",   "branch",       "packet_loss","restart_bgp"),
    ]
    fails = 0
    for sev, role, hint, expected in cases:
        result = decide(sev, role, "X", hint, None)
        got = result["action"]
        status = "PASS" if got == expected else "FAIL"
        if got != expected:
            fails += 1
        print(f"  {status}: sev={sev:8s} role={role:12s} hint={str(hint):12s} "
              f"-> {got:18s} (expected {expected})")
    print()
    if fails:
        print(f"FAILED: {fails} case(s)")
        return 1
    print(f"ALL {len(cases)} DECISION CASES PASS")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Decision Tree (C4)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_decide = sub.add_parser("decide", help="Choose action for one incident")
    p_decide.add_argument("--severity", required=True)
    p_decide.add_argument("--root-role", required=True)
    p_decide.add_argument("--root-router", default="")
    p_decide.add_argument("--fault-hint", default=None)
    p_decide.add_argument("--history-match", type=float, default=None)

    sub.add_parser("test", help="Run built-in decision table tests")

    args = parser.parse_args()
    if args.command == "decide":
        sys.exit(cmd_decide(args))
    if args.command == "test":
        sys.exit(cmd_test(args))


if __name__ == "__main__":
    main()
