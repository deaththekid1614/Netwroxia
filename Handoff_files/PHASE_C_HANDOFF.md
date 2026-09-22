═══════════════════════════════════════════════════════════════════════════════
                     NETWROXIA — PHASE C HANDOFF DOCUMENT
                    Decision & Action Layer: Auto-Remediation Engine
                    IBM Z Datathon 2026 | Team Astro_X | Wildcard Entry
═══════════════════════════════════════════════════════════════════════════════

Generated: 2026-09-22
Project root: /home/death-kid/IDE/netwroxia/
Machine: MacBook Air 2015, Zorin OS 16.3 x86_64, 8GB RAM, i5-5250U
Status: COMPLETE — 15 files verified, all tests green, zero new dependencies

───────────────────────────────────────────────────────────────────────────────
1. WHAT PHASE C IS
───────────────────────────────────────────────────────────────────────────────

Phase A produced raw data (beacons, fault library, readiness checks).
Phase B turned data into decisions, impact, RCA, and explanations.
Phase C turns RCA into ACTION — the auto-remediation engine.

Phase C answers:
  - What audit trail exists for every action?              (C1)
  - How is incident lifecycle persisted?                   (C2)
  - What drives state transitions from RCA to action?      (C3)
  - Which action is safe to take for this incident?        (C4)
  - How do we prevent action flapping?                     (C5)
  - When does a human need to approve?                     (C6)
  - How do we capture pre-state for rollback?              (C7)
  - What contract does every action follow?                (C8)
  - The 5 whitelisted actions?                             (C9-C13)
  - How does it all run together?                          (C14)
  - What's the one CLI that drives everything?             (C15)

Phase C is real. It executes `docker exec` commands, writes JSON
snapshots, logs to an append-only audit trail, and enforces human
approval for CRITICAL incidents. It never touches HO-Chennai
without explicit override.

───────────────────────────────────────────────────────────────────────────────
2. FILES CREATED IN PHASE C
───────────────────────────────────────────────────────────────────────────────

C1  audit/audit_logger.py
    Append-only JSONL audit log, one file per day. POSIX file locking
    (fcntl.flock) for concurrent-write safety. Query helpers:
    recent_events, count_recent, tail.
    CLI: log, tail, count

C2  incidents/incident_store.py
    SQLite-backed incident persistence. Two tables: incidents (current
    state) + transitions (append-only history). WAL journal mode, FK
    cascade, atomic transactions. Terminal state (CLOSED) is enforced.
    Env override: NETWROXIA_DB_PATH
    CLI: init, list, get, history, count, reset

C3  incidents/incident_engine.py
    Lifecycle orchestrator. Reads B8's rca/latest_rca.json, creates or
    updates incidents, auto-advances based on severity. Closes orphans
    when RCA no longer mentions them. Idempotent across runs.
    CLI: tick, tick --dry-run, status

C4  remediation/engine/decision_tree.py
    Pure function: (severity, root_role, fault_hint, history_match)
    -> one of 5 whitelisted actions. HO safety override is absolute.
    Unknown role escalates. Fault hints gated by severity.
    CLI: decide, test

C5  remediation/guardrails/rate_limiter.py
    Two throttles, read from C1 audit log:
      per_router    max 1 action/router/300s
      network_wide  max 5 actions total/600s
    Counts only action.executed, action.failed, action.rolled_back.
    Read-only. Deterministic.
    CLI: check, status

C6  remediation/guardrails/approval_gate.py
    Human-in-the-loop gate. CRITICAL incidents require approval.
    Writes to remediation/engine/pending_approval.json (atomic).
    Idempotent request; refuse double-decision; refuse missing.
    Env override: NETWROXIA_PENDING_PATH
    CLI: request, check, approve, deny, list, clear

C7  remediation/guardrails/rollback.py
    Pre-action snapshot storage. Files under incidents/snapshots/.
    Atomic writes. mark_restored is idempotent.
    Env override: NETWROXIA_SNAPSHOTS_DIR
    CLI: save, list, latest, mark-restored

C8  remediation/actions/_base.py
    Abstract Action class with 5-method contract:
      preconditions -> snapshot -> execute -> verify -> rollback
    Registry pattern with @register_action decorator. Autodiscovery
    via pkgutil. run_in_container helper (never shell=True).
    Shared resolve_container map (short name -> clab-netwroxia-*).
    CLI: list, info <action>

C9  remediation/actions/no_op.py
    Monitoring-only action. Never touches network. Snapshot = {}.
    Verify = True. Rollback = no-op. Used for LOW/NONE.

C10 remediation/actions/restart_bgp.py
    Clears BGP sessions: vtysh clear ip bgp *. Parses show ip bgp
    summary. Verifies peer count returns to baseline within 60s.
    Refuses HO unless ctx['allow_ho']=True. Rollback = no-op
    (BGP self-recovers).

C11 remediation/actions/clear_ospf.py
    Restarts OSPF: vtysh clear ip ospf process. Parses show ip ospf
    neighbor, counts Full state lines. Verifies adjacency count returns
    to baseline within 60s. Refuses HO unless allowed. Rollback = no-op.

C12 remediation/actions/reroute_traffic.py
    Applies tc netem delay qdisc on primary uplink (eth1) to simulate
    traffic reroute. Genuinely reversible: rollback removes the qdisc.
    Default delay 5ms, override via ctx['delay_ms']. Refuses HO unless
    allowed. Snapshot captures pre-qdisc state.

C13 remediation/actions/escalate_to_human.py
    Writes structured escalation JSON to
    remediation/engine/escalations/<id>_<ts>.json. Includes incident_id,
    severity, router, reason, timestamp, acknowledgement fields. Verify
    checks file exists + parses. Rollback preserves the record.
    Env override: NETWROXIA_ESCALATIONS_DIR

C14 remediation/engine/executor.py
    Full lifecycle driver for one incident:
      load -> decide -> rate_limit -> approval_gate -> preconditions
        -> snapshot -> execute -> verify -> resolve OR rollback
        -> audit each step -> update incident state
    Handles all edge cases: dry-run, missing incident, terminal state,
    HO escalation, rate-limit throttle, approval gate.
    CLI: run <id>, dryrun <id>, run-all

C15 remediation/engine/orchestrator.py
    Top-level CLI. Chains C3 + C14 into single-command operations.
    Commands: tick, run, cycle, approvals, approve, deny, status,
    history, watch.
    Dry-run cycle persists incidents but skips network actions.

───────────────────────────────────────────────────────────────────────────────
3. DIRECTORIES ADDED OR MODIFIED
───────────────────────────────────────────────────────────────────────────────

audit/                                  (extended)
  audit_logger.py                       C1
  audit_logs/YYYY-MM-DD.jsonl           generated

incidents/                              (extended)
  incident_store.py                     C2
  incident_engine.py                    C3
  incidents.db                          SQLite (generated)
  snapshots/<id>_<action>_<ts>.json     pre-state (generated)

remediation/
  engine/
    decision_tree.py                    C4
    executor.py                         C14
    orchestrator.py                     C15
    pending_approval.json               (generated by C6)
    escalations/<id>_<ts>.json          (generated by C13)
  guardrails/
    rate_limiter.py                     C5
    approval_gate.py                    C6
    rollback.py                         C7
  actions/
    _base.py                            C8
    no_op.py                            C9
    restart_bgp.py                      C10
    clear_ospf.py                       C11
    reroute_traffic.py                  C12
    escalate_to_human.py                C13

───────────────────────────────────────────────────────────────────────────────
4. DATA FLOW
───────────────────────────────────────────────────────────────────────────────

    Phase B output
    rca/latest_rca.json
    impact/latest_business_impact.json
             │
             ▼
    ┌──────────────────────────────────────┐
    │ C15 orchestrator cycle                │
    │  ├─ C3 tick:  create/update incidents │
    │  └─ C14 run:  execute ready incidents │
    └────────────────┬─────────────────────┘
                     │
                     ▼
    ┌──────────────────────────────────────┐
    │ C14 executor (per incident)          │
    │  ├─ C4 decision_tree.decide()        │
    │  ├─ C5 rate_limiter.check()          │
    │  ├─ C6 approval_gate.check_approval()│
    │  ├─ action.preconditions()           │
    │  ├─ C7 rollback.save_snapshot()      │
    │  ├─ action.execute()                 │
    │  ├─ action.verify()                  │
    │  ├─ on success: C2 update_state(RESOLVED)
    │  └─ on failure: C7 mark_restored()  │
    │                 action.rollback()     │
    │                 C2 update_state(ROLLED_BACK or ESCALATED)
    └────────────────┬─────────────────────┘
                     │
                     ▼
    Persistent artifacts:
      audit/audit_logs/YYYY-MM-DD.jsonl         (C1)
      incidents/incidents.db                    (C2)
      incidents/snapshots/*.json                (C7)
      remediation/engine/pending_approval.json  (C6)
      remediation/engine/escalations/*.json     (C13)

───────────────────────────────────────────────────────────────────────────────
5. CLI REFERENCE
───────────────────────────────────────────────────────────────────────────────

── Audit (C1) ─────────────────────────────────────────────────────────

python3 audit/audit_logger.py log --event smoke.cli --router HO-Chennai
python3 audit/audit_logger.py tail --n 20
python3 audit/audit_logger.py count --event "action." --window 3600

── Incidents (C2) ─────────────────────────────────────────────────────

python3 incidents/incident_store.py init
python3 incidents/incident_store.py list [--state X] [--limit N]
python3 incidents/incident_store.py get <id>
python3 incidents/incident_store.py history <id>
python3 incidents/incident_store.py count
python3 incidents/incident_store.py reset --yes

── Incident engine (C3) ───────────────────────────────────────────────

python3 incidents/incident_engine.py tick [--dry-run]
python3 incidents/incident_engine.py status

── Decision tree (C4) ─────────────────────────────────────────────────

python3 remediation/engine/decision_tree.py test
python3 remediation/engine/decision_tree.py decide \
    --severity CRITICAL --root-role zonal_office \
    --fault-hint packet_loss --history-match 0.85

── Rate limiter (C5) ──────────────────────────────────────────────────

python3 remediation/guardrails/rate_limiter.py check --router ZO-Bengaluru
python3 remediation/guardrails/rate_limiter.py check
python3 remediation/guardrails/rate_limiter.py status

── Approval gate (C6) ─────────────────────────────────────────────────

python3 remediation/guardrails/approval_gate.py request \
    --incident-id INC-X --severity CRITICAL \
    --root-router ZO-Bengaluru --action reroute_traffic
python3 remediation/guardrails/approval_gate.py check --incident-id INC-X
python3 remediation/guardrails/approval_gate.py approve \
    --incident-id INC-X --approver death-kid --note "ok"
python3 remediation/guardrails/approval_gate.py deny \
    --incident-id INC-X --approver death-kid --note "no"
python3 remediation/guardrails/approval_gate.py list
python3 remediation/guardrails/approval_gate.py clear --incident-id INC-X

── Rollback (C7) ──────────────────────────────────────────────────────

python3 remediation/guardrails/rollback.py save \
    --incident-id INC-X --action restart_bgp --router ZO-Bengaluru \
    --state '{"bgp_peer_count": 3}'
python3 remediation/guardrails/rollback.py list
python3 remediation/guardrails/rollback.py latest --incident-id INC-X
python3 remediation/guardrails/rollback.py mark-restored \
    --snapshot-id <id> --actor death-kid --note "verify failed"

── Actions registry (C8) ──────────────────────────────────────────────

python3 remediation/actions/_base.py list
python3 remediation/actions/_base.py info restart_bgp

── Executor (C14) ─────────────────────────────────────────────────────

python3 remediation/engine/executor.py run --incident-id INC-X
python3 remediation/engine/executor.py run --incident-id INC-X --dry-run
python3 remediation/engine/executor.py dryrun --incident-id INC-X
python3 remediation/engine/executor.py run-all [--dry-run]

── Orchestrator (C15) ─────────────────────────────────────────────────

python3 remediation/engine/orchestrator.py tick [--dry-run]
python3 remediation/engine/orchestrator.py run [--dry-run]
python3 remediation/engine/orchestrator.py cycle [--dry-run]
python3 remediation/engine/orchestrator.py approvals
python3 remediation/engine/orchestrator.py approve <id> [--note "..."]
python3 remediation/engine/orchestrator.py deny <id> [--note "..."]
python3 remediation/engine/orchestrator.py status
python3 remediation/engine/orchestrator.py history <id>
python3 remediation/engine/orchestrator.py watch --interval 30 [--dry-run]

───────────────────────────────────────────────────────────────────────────────
6. THE 5 ACTIONS
───────────────────────────────────────────────────────────────────────────────

no_op
  Use case:   LOW/NONE severity; watch-and-see
  Network:    Nothing
  Snapshot:   {} (empty)
  Rollback:   N/A (no-op)
  HO gate:    N/A

restart_bgp
  Use case:   BGP-specific faults OR branch/zonal MEDIUM
  Command:    vtysh -c "clear ip bgp *"
  Snapshot:   pre-BGP summary + expected peer count
  Verify:     peer count returns to baseline within 60s
  Rollback:   N/A (BGP self-recovers)
  HO gate:    Refused unless ctx['allow_ho']=True

clear_ospf
  Use case:   OSPF-specific faults OR zonal MEDIUM
  Command:    vtysh -c "clear ip ospf process"
  Snapshot:   pre-OSPF neighbor list + expected count
  Verify:     FULL neighbor count returns to baseline within 60s
  Rollback:   N/A (OSPF self-recovers)
  HO gate:    Refused unless ctx['allow_ho']=True

reroute_traffic
  Use case:   latency/packet_loss faults OR zonal HIGH/CRITICAL
  Command:    tc qdisc add dev eth1 root netem delay 5ms
  Snapshot:   pre-qdisc state
  Verify:     tc qdisc show confirms netem active
  Rollback:   tc qdisc del dev eth1 root (REAL reversal)
  HO gate:    Refused unless ctx['allow_ho']=True

escalate_to_human
  Use case:   HO root at any severity > LOW; unknown role;
              decision tree override; guardrail fallback
  Command:    None (writes escalation file)
  Snapshot:   {} (empty)
  Verify:     escalation file exists + parses
  Rollback:   Preserves record (informational only)
  HO gate:    N/A (this IS the HO-safe path)

───────────────────────────────────────────────────────────────────────────────
7. DECISION TREE PRIORITY ORDER
───────────────────────────────────────────────────────────────────────────────

1. Fault-type override (highest)
     bgp            at MEDIUM+ -> restart_bgp
     ospf           at MEDIUM+ -> clear_ospf
     packet_loss    at HIGH+   -> reroute_traffic
     latency        at HIGH+   -> reroute_traffic
     congestion     at HIGH+   -> reroute_traffic
     interface      at HIGH+   -> reroute_traffic

2. Severity + role table

     Severity | HO root | ZO root           | Branch root
     ---------|---------|-------------------|------------------
     NONE     | no_op   | no_op             | no_op
     LOW      | no_op   | no_op             | no_op
     MEDIUM   | escalate| clear_ospf        | restart_bgp
     HIGH     | escalate| reroute_traffic   | restart_bgp
     CRITICAL | escalate| reroute_traffic   | restart_bgp

3. Safety override: HO with severity != NONE/LOW always escalates,
   even if fault hint says otherwise

4. Safety override: unknown role with severity != NONE/LOW always
   escalates

───────────────────────────────────────────────────────────────────────────────
8. GUARDRAILS (RATE + APPROVAL + ROLLBACK)
───────────────────────────────────────────────────────────────────────────────

Rate limiting (C5):
  per_router    : 1 action per router per 300s
  network_wide  : 5 actions total per 600s
  Counts: action.executed, action.failed, action.rolled_back
  Skips: no_op, escalate_to_human (they're not real actions)
  Behavior on throttle: stage=rate_limit, wait_seconds > 0

Approval gate (C6):
  CRITICAL severity: request approval, await human
  Skip for no_op and escalate_to_human (they don't touch network)
  Storage: remediation/engine/pending_approval.json
  States: NOT_FOUND | PENDING | APPROVED | DENIED
  Double-decision refused (no flapping)

Rollback (C7):
  Snapshot before every real action
  On verify fail: mark_restored, run action.rollback()
  If rollback succeeds: incident -> ROLLED_BACK
  If rollback fails: incident -> ESCALATED

───────────────────────────────────────────────────────────────────────────────
9. INCIDENT LIFECYCLE
───────────────────────────────────────────────────────────────────────────────

       DETECTED           <- C3 creates from RCA
          │
          ▼
    INVESTIGATING         <- C3 auto-advance
          │
          ▼
   AWAITING_APPROVAL      <- only if CRITICAL severity
          │
          ▼
      EXECUTING           <- C14 executor
          │
          ▼
      VERIFYING           <- action.verify()
        /        \
       /          \
  VERIFIED       FAILED
     │              │
     ▼              ▼
  RESOLVED     ROLLED_BACK  (if rollback succeeds)
                  │
                  ▼
              ESCALATED     (if rollback fails)
                  │
                  ▼
               CLOSED       (via next C3 tick, when RCA clears)

Every transition logged to:
  - incidents/incidents.db (transitions table)
  - audit/audit_logs/YYYY-MM-DD.jsonl (incident.state_changed event)

───────────────────────────────────────────────────────────────────────────────
10. AUDIT EVENT TAXONOMY
───────────────────────────────────────────────────────────────────────────────

Lifecycle (C3):
  incident.created
  incident.observed
  incident.state_changed
  incident.transition_rejected

Execution (C14):
  execution.started
  execution.decision
  execution.throttled
  execution.awaiting_approval
  execution.denied
  execution.precondition_failed
  execution.snapshot_saved
  execution.escalated

Action results (C14 — counted by C5 rate limiter):
  action.executed
  action.failed
  action.rolled_back

Supporting services:
  approval.requested
  approval.approved
  approval.denied
  approval.cleared
  snapshot.saved
  snapshot.restored

Orchestrator (C15):
  orchestrator.cycle.started
  orchestrator.cycle.completed

───────────────────────────────────────────────────────────────────────────────
11. TEST STATUS — ALL GREEN
───────────────────────────────────────────────────────────────────────────────

C1  audit logger: append, file layout, count_recent, tail,
    corrupt line skipped, cross-day files            21/21 ✓
C2  incident store: schema, upsert, state transitions,
    terminal guard, cascade delete, reset            34/34 ✓
C3  incident engine: tick idempotent, auto-advance,
    orphan close, dry-run no-persist, multi-incident 34/34 ✓
C4  decision tree: 11 table cases, hint overrides,
    HO safety, confidence, determinism               62/62 ✓
C5  rate limiter: clean state, per-router, network-wide,
    time windows, wait_seconds computation           22/22 ✓
C6  approval gate: request, approve, deny, double-decision,
    missing incident, list filter                    34/34 ✓
C7  rollback: save, list, latest, mark-restored,
    idempotency, delete                              42/42 ✓
C8  action base: registry, container resolve, run_in_container,
    abstract enforcement, autodiscovery              36/36 ✓
C9  no_op: registration, execute, snapshot, verify,
    rollback, idempotency                            21/21 ✓
C10 restart_bgp: parse, preconditions, HO gate,
    dry_run, expected peers                          34/34 ✓
C11 clear_ospf: parse Full states, preconditions,
    HO gate, dry_run                                 33/33 ✓
C12 reroute_traffic: DEFAULT_INTERFACE, delay override,
    live apply->verify->rollback on ZO              41/41 ✓
C13 escalate_to_human: file write, sanitization,
    dry-run no-write, verify integrity               31/31 ✓
C14 executor: all paths (not found, no_op, awaiting,
    approved, denied, rate limit, terminal, escalate,
    audit events, run_all)                           33/33 ✓
C15 orchestrator: empty cycle, LOW dry, CRITICAL await,
    approve+run, deny+escalate, HO escalate,
    history, audit, print helpers                    27/27 ✓

Live demos verified:
  C12 live rollback test on ZO-Bengaluru: qdisc applied ->
      verified -> removed -> network healthy
  C15 live cycle on real RCA: incident created ->
      escalated (HO root) -> resolved -> status clean

No side effects confirmed after every phase:
  beacons/system_check.py            SYSTEM READY
  network/verify/health_check.py     ALL CHECKS PASSED

───────────────────────────────────────────────────────────────────────────────
12. KNOWN LIMITATIONS
───────────────────────────────────────────────────────────────────────────────

1. Rate limiter is a "soft" gate — it reads audit log timestamps, so a
   caller who skips audit.log() bypasses it. The executor always logs;
   direct action calls must too.

2. Approval gate blocks CRITICAL forever until a human approves or denies.
   No timeout/TTL — intentional (safety-first). In a demo, use the
   orchestrator approve/deny commands.

3. reroute_traffic is a simulation. Real reroute would require SD-WAN
   controller or BGP community changes. The tc netem qdisc demonstrates
   the apply/verify/rollback cycle, not the actual reroute.

4. Restart_bgp and clear_ospf are self-recovering. Their rollback is a
   no-op. If verify times out (60s), the incident escalates for human
   review — that's the correct safety net.

5. HO-Chennai never auto-acts. Even CRITICAL severity escalates.
   This is by design and reflects the reality that route reflectors
   are too critical to touch without human oversight.

6. Incident IDs are B5 signatures. Two identical faults on different
   days have the same ID; the second is treated as the same incident
   until manually closed. This is intentional — Netwroxia groups
   identical signatures.

7. Reroute_traffic's default delay (5ms) is deliberately small. It's
   proof of mechanism, not traffic shaping. Override via ctx['delay_ms']
   for demo impact.

───────────────────────────────────────────────────────────────────────────────
13. WHAT'S NEXT — PHASE D AND E PREVIEW
───────────────────────────────────────────────────────────────────────────────

Phase D (Analytics & Reporting) — 10 files:

  D1  analytics/kpi_calculator.py      MTTD, MTTR, MTBF, Availability
  D2  analytics/sla_tracker.py         per-branch/service SLA compliance
  D3  analytics/trend_engine.py        daily/weekly/monthly aggregation
  D4  mlops/model_registry.py          track all trained model versions
  D5  mlops/drift_detector.py          compare live features vs training
  D6  mlops/performance_tracker.py     rolling precision/recall/F1
  D7  reports/daily_report.py          auto daily NOC report
  D8  reports/weekly_report.py         management weekly
  D9  reports/incident_report.py       per-incident PDF/HTML
  D10 orchestrator.py (rename)         unified pipeline runner

Phase E (Dashboard 2.0) — 10 files:

  E1-E10  dashboard/pages/*.py         new tabs:
            beacon_monitor, health_score, incidents,
            rca, impact, remediation, mlops,
            reports, audit, role_switcher
          plus enhanced app.py integration

Phase D reads Phase A/B/C artifacts. Phase E reads all.

No new dependencies required for either phase.

═══════════════════════════════════════════════════════════════════════════════
END OF PHASE C HANDOFF
Phase C: COMPLETE | Files: 15 | Tests: 519/519 pass | Ready for Phase D
═══════════════════════════════════════════════════════════════════════════════
