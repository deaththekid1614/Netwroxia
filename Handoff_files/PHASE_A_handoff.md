═══════════════════════════════════════════════════════════════════════════════
                     NETWROXIA — PHASE A HANDOFF DOCUMENT
              Data Foundation: Beacons + Fault Library + Sync Utility
              IBM Z Datathon 2026 | Team Astro_X | Wildcard Entry
═══════════════════════════════════════════════════════════════════════════════

Generated: 2026-09-22
Project root: /home/death-kid/IDE/netwroxia/
Machine: MacBook Air 2015, Zorin OS 16.3 x86_64, 8GB RAM, i5-5250U
Workflow: VS Code + terminal, chunk-by-chunk paste
Status: COMPLETE — all 7 files verified, all tests green

───────────────────────────────────────────────────────────────────────────────
1. WHAT PHASE A IS
───────────────────────────────────────────────────────────────────────────────

Phase A adds the "data foundation" layer that every downstream phase needs.
Before Phase A, the system had:
  - Network simulation (Stage 1)
  - Telegraf -> InfluxDB telemetry (Stage 2)
  - ML predictions from InfluxDB (Stage 3)
  - LLM copilot (Stage 4)
  - Streamlit dashboard (Stage 5)
  - No fault injection library beyond a broken primitive script
  - No beacon/heartbeat system
  - No system readiness check
  - Telegraf's ping targets went stale after every containerlab redeploy

Phase A fixes all four gaps. It also locks down a clean failure/recovery
workflow so later phases (RCA, remediation) can use it safely.

───────────────────────────────────────────────────────────────────────────────
2. FILES CREATED IN PHASE A
───────────────────────────────────────────────────────────────────────────────

A1  beacons/beacon_schema.json
    Schema spec: fields, tags, status thresholds, baseline params, router list.

A2  beacons/beacon_sender.py
    Host-side agent. Execs `ping` inside each sender router toward HO's
    loopback (10.255.0.1), parses RTT/loss, computes status_code, writes
    'beacon' measurement to InfluxDB. Tracks monotonic seq in
    beacons/.beacon_seq.json.
    CLI: --once, --interval N, --count N

A3  beacons/baseline_engine.py
    Reads last 60min of 'beacon' rtt_ms where rtt > 0, groups by src_router,
    computes mean and stddev. Writes beacons/latest_baselines.json.
    Falls back to schema.fallback_rtt_ms (25.0) if sample_count < min_samples.
    CLI: --once, --interval N, --min-samples N (override for testing)

A4  beacons/beacon_collector.py
    Reads A2's raw state + A3's baselines. Applies consecutive-miss
    detection (3 misses -> MISSING). Computes per-router z-score.
    Emits canonical beacons/latest_beacon_health.json.
    Persistent state: beacons/.beacon_miss_counter.json.
    Idempotent: skips a router whose seq was already processed.

A4b beacons/system_check.py
    Read-only readiness check. Verifies containerlab containers running,
    InfluxDB reachable, OSPF FULL on HO, BGP Established on HO.
    Exit 0 = READY, exit 1 = NOT READY.
    CLI: --wait, --timeout N (default 180s, 10s poll interval)

A5  fault_sim/scenario_library.py
    14 fault scenarios with apply()/reset(). CLI subcommands:
      list, status, apply, reset, reset-all, recover-topology
    Tracks active faults in fault_sim/active_faults.json.
    container_stop is marked DESTRUCTIVE (recovery via recover-topology).

A6  telemetry/update_telegraf_targets.py
    Regenerates the [inputs.ping] urls array in telegraf.conf from the
    current containerlab container IPs. Backs up original to
    telegraf.conf.bak before first edit. Restarts telegraf via
    docker-compose after writing.
    CLI: --dry-run, --no-restart

───────────────────────────────────────────────────────────────────────────────
3. DIRECTORIES ADDED
───────────────────────────────────────────────────────────────────────────────

beacons/                 (new)
  beacon_schema.json
  beacon_sender.py
  baseline_engine.py
  beacon_collector.py
  system_check.py
  latest_baselines.json         (generated, runtime)
  latest_beacon_state.json      (generated, runtime)
  latest_beacon_health.json     (generated, runtime)
  .beacon_seq.json              (generated, hidden state)
  .beacon_miss_counter.json     (generated, hidden state)

fault_sim/               (new)
  scenario_library.py
  active_faults.json            (generated, runtime)

telemetry/               (existing, 1 file added)
  update_telegraf_targets.py    (new)
  telegraf.conf                 (edited by A6 — backup at telegraf.conf.bak)

───────────────────────────────────────────────────────────────────────────────
4. DATA FLOW
───────────────────────────────────────────────────────────────────────────────

                  Every 10s (or on demand):
                         │
                         ▼
    ┌──────────────────────────────────────────┐
    │ A2  beacon_sender.py                     │
    │   docker exec <router> ping -c3 10.255.0.1│
    │   ├─ writes 'beacon' to InfluxDB         │
    │   ├─ writes beacons/latest_beacon_state  │
    │   └─ bumps .beacon_seq.json              │
    └────────────────────┬─────────────────────┘
                         │
          Every 60s (or on demand):
                         │
                         ▼
    ┌──────────────────────────────────────────┐
    │ A3  baseline_engine.py                   │
    │   SELECT mean(rtt_ms), stddev(rtt_ms)    │
    │   FROM beacon WHERE rtt_ms > 0           │
    │   → beacons/latest_baselines.json        │
    └────────────────────┬─────────────────────┘
                         │
                (A2 reads this next cycle)
                         │
          Every 10s (or on demand):
                         │
                         ▼
    ┌──────────────────────────────────────────┐
    │ A4  beacon_collector.py                  │
    │   reads A2 raw state + A3 baselines      │
    │   applies consecutive-miss logic         │
    │   → beacons/latest_beacon_health.json    │
    └──────────────────────────────────────────┘

    A4b system_check.py
        Read-only. Checks containers + InfluxDB + OSPF + BGP.
        Called by orchestrator (Phase D) before any pipeline run.

    A5 scenario_library.py
        Injects faults (tc netem, docker stop/pause, vtysh clears).
        Used by ML training (generate labeled data) and demo.

    A6 update_telegraf_targets.py
        Run after every `containerlab deploy` to resync telegraf's
        ping targets to current container IPs.

───────────────────────────────────────────────────────────────────────────────
5. COMMANDS REFERENCE
───────────────────────────────────────────────────────────────────────────────

── Beacons ─────────────────────────────────────────────────────────────────

# Send one cycle of beacons
python3 beacons/beacon_sender.py --once

# Send beacons continuously
python3 beacons/beacon_sender.py --interval 10

# Compute rolling baselines (production: needs 30+ samples)
python3 beacons/baseline_engine.py --once

# Compute baselines with lower threshold (testing)
python3 beacons/baseline_engine.py --once --min-samples 3

# Enrich beacon state (writes health JSON)
python3 beacons/beacon_collector.py

# System readiness (single check)
python3 beacons/system_check.py

# System readiness (poll until ready, max 180s)
python3 beacons/system_check.py --wait

# System readiness (custom timeout)
python3 beacons/system_check.py --wait --timeout 300

── Faults ──────────────────────────────────────────────────────────────────

# List all scenarios + links + containers
python3 fault_sim/scenario_library.py list

# Show currently active faults
python3 fault_sim/scenario_library.py status

# Apply latency on link
python3 fault_sim/scenario_library.py apply latency --link ho-zo --value 100

# Apply loss on link
python3 fault_sim/scenario_library.py apply loss --link zo-ho --value 30

# Apply congestion (rate-limit in kbit)
python3 fault_sim/scenario_library.py apply congestion --link ho-zo --value 1000

# Pause a container (reversible)
python3 fault_sim/scenario_library.py apply container_pause --container zo-bengaluru
python3 fault_sim/scenario_library.py reset container_pause --container zo-bengaluru

# Stop a container (DESTRUCTIVE — veth links destroyed)
python3 fault_sim/scenario_library.py apply container_stop --container zo-bengaluru
python3 fault_sim/scenario_library.py recover-topology    # <-- to restore

# Reset one fault
python3 fault_sim/scenario_library.py reset latency --link ho-zo

# Reset every recorded fault
python3 fault_sim/scenario_library.py reset-all

── Telegraf Sync ───────────────────────────────────────────────────────────

# Preview what would change
python3 telemetry/update_telegraf_targets.py --dry-run

# Write + restart telegraf
python3 telemetry/update_telegraf_targets.py

# Write without restart
python3 telemetry/update_telegraf_targets.py --no-restart

── Verification ────────────────────────────────────────────────────────────

# Stage 1 network health (existing)
python3 network/verify/health_check.py

# InfluxDB ping query (verify targets)
curl -sG "http://localhost:8086/query?db=netwroxia" \
  --data-urlencode "q=SELECT url, average_response_ms, percent_packet_loss FROM ping WHERE time > now() - 1m" \
  | python3 -m json.tool

───────────────────────────────────────────────────────────────────────────────
6. THE 14 FAULT SCENARIOS
───────────────────────────────────────────────────────────────────────────────

Link-scoped (require --link):
  1.  latency              — tc netem delay Xms
  2.  loss                 — tc netem loss X%
  3.  congestion           — tc tbf rate Xkbit (default 1mbit)
  4.  jitter               — tc netem delay Xms 20ms distribution normal
  5.  bandwidth_flood      — severe tbf rate 50kbit
  6.  blackhole            — ip route add blackhole <target>
  7.  interface_down       — ip link set <iface> down
  8.  gradual_degradation  — ramp 10->100ms over 60s (background)

Container-scoped (require --container):
  9.  container_stop       — docker stop (DESTRUCTIVE)
  10. container_pause      — docker pause (reversible)
  11. bgp_peer_reset       — vtysh clear ip bgp *
  12. ospf_adjacency_reset — vtysh clear ip ospf process
  13. cpu_stress           — 2x yes loops, 120s, background
  14. mem_stress           — 200MB Python alloc, 120s, background

Link names (6):
  ho-zo    (ho-chennai eth1 -> ZO)
  zo-ho    (zo-bengaluru eth1 -> HO)
  zo-kora  (zo-bengaluru eth2 -> BR-Koramangala)
  kora-zo  (br-koramangala eth1 -> ZO)
  zo-white (zo-bengaluru eth3 -> BR-Whitefield)
  white-zo (br-whitefield eth1 -> ZO)

Interface map (verified against topology.yml):
  ho-chennai     : eth0 = mgmt, eth1 = data
  zo-bengaluru   : eth0 = mgmt, eth1 = data->HO, eth2 = data->BR-Kora,
                   eth3 = data->BR-White
  br-koramangala : eth0 = mgmt, eth1 = data->ZO
  br-whitefield  : eth0 = mgmt, eth1 = data->ZO

───────────────────────────────────────────────────────────────────────────────
7. BEACON STATUS SEMANTICS
───────────────────────────────────────────────────────────────────────────────

Raw status (from A2, per cycle):
  0 = OK        — RTT <= 1.5x baseline AND loss <= 1%
  1 = DEGRADED  — RTT <= 3.0x baseline AND loss <= 10%
  2 = CRITICAL  — RTT > 3.0x baseline OR loss > 10%
  3 = MISSING   — loss >= 100% OR no packets received

Final status (from A4, after consecutive-miss check):
  Raw 0,1,2     — passed through unchanged
  Raw 3, count<3 — DOWNGRADED to CRITICAL (transient blip suppression)
  Raw 3, count>=3 — PROMOTED to MISSING (confirmed outage)

Network score (0-100):
  OK = 100, DEGRADED = 70, CRITICAL = 30, MISSING = 0
  Score = rounded mean across all sender routers

Persistent counter (beacons/.beacon_miss_counter.json):
  Reset to 0 on any successful ping
  Increments by 1 on any failed ping
  Idempotent — same seq does not double-increment

───────────────────────────────────────────────────────────────────────────────
8. INTEGRATION POINTS WITH EXISTING STAGES
───────────────────────────────────────────────────────────────────────────────

Stage 1 (network)          — Read-only. No files touched.
                             A5 uses docker exec / docker stop on containers.
                             A4b reads container state via docker ps.

Stage 2 (telemetry)        — telegraf.conf edited by A6 (backup exists).
                             InfluxDB is written to by A2 (beacon measurement).

Stage 3 (ML)               — Untouched. Reads InfluxDB as before.
                             A2's beacon measurement is additional data ML
                             can optionally consume in Phase B.

Stage 4 (copilot)          — Untouched.
                             A4's latest_beacon_health.json is available
                             for Phase B (copilot will read it).

Stage 5 (dashboard)        — Untouched.
                             A4's latest_beacon_health.json is available
                             for Phase B (new Beacon tab).

Stage 6 (remediation)      — Untouched.
                             A5 provides the fault primitives that Phase C
                             will call for validation.

───────────────────────────────────────────────────────────────────────────────
9. TEST STATUS — ALL GREEN
───────────────────────────────────────────────────────────────────────────────

A1  schema loads, JSON parses                        ✓
A2  beacons written to InfluxDB, seq increments      ✓
    status transitions OK/DEGRADED/CRITICAL/MISSING  ✓
    idempotent on --once repeat                      ✓
A3  fallback path (no samples)                       ✓
    rolling path (with --min-samples 3)              ✓
    JSON schema correct                              ✓
A4  consecutive-miss detection (1->2->3)             ✓
    idempotency (same seq = no double increment)     ✓
    network score calculation                        ✓
A4b single check + --wait + timeout all work         ✓
    negative case (stop telegraf) detected           ✓
A5  latency fault injection -> beacon detects        ✓
    loss fault injection -> beacon detects           ✓
    container_stop warning + reset refusal           ✓
    container_pause reversible                       ✓
    recover-topology restores veth links             ✓
    reset-all clears state                           ✓
A6  dry-run preview                                  ✓
    backup created                                   ✓
    config updated                                   ✓
    telegraf restarted                               ✓
    idempotent                                       ✓
    health_check all 4 categories PASS               ✓

───────────────────────────────────────────────────────────────────────────────
10. KNOWN LIMITATIONS / DESIGN DECISIONS
───────────────────────────────────────────────────────────────────────────────

1. Containerlab redeploy shuffles container IPs.
   Mitigation: A6 resyncs telegraf targets. Beacons unaffected (use loopbacks).
   Beacons are immune to IP shuffling by design.

2. container_stop destroys veth pairs (fundamental containerlab behavior).
   Mitigation: marked DESTRUCTIVE, recover-topology rebuilds.
   Preferred alternative: container_pause (reversible, no rebuild).

3. OSPF+BGP need 90-120s to converge after redeploy.
   Mitigation: A4b --wait polls until ready.
   Do not run pipeline in the first 90s after deploy.

4. Baseline fallback (25.0ms) is calibrated for real WAN, not
   containerlab (sub-ms RTT). Once 30+ real samples accumulate,
   rolling baseline replaces fallback and deviation becomes meaningful.
   Expected in a normal 5-min production run.

5. A5's gradual_degradation runs in background (nohup inside container).
   Not tracked in active_faults.json (self-clearing after 60s).
   reset on this scenario is a no-op after completion.

6. A5's cpu_stress / mem_stress also run in background (120s self-clear).
   pkill cleanup in reset is best-effort.

───────────────────────────────────────────────────────────────────────────────
11. WHAT'S NEXT — PHASE B PREVIEW
───────────────────────────────────────────────────────────────────────────────

Phase B (Intelligence Layer) will add:

  B1   analytics/health_score.py       — 0-100 score per router/branch/region
  B2   impact/service_map.json         — router -> banking services mapping
  B3   impact/impact_analyzer.py       — affected services + user count
  B4   impact/business_impact.py       — INR loss estimator
  B5   rca/correlation_engine.py       — group anomalies in time window
  B6   rca/causal_graph.py             — NetworkX propagation
  B7   rca/historical_matcher.py       — match against past incidents
  B8   rca/rca_output.py               — emit rca/latest_rca.json
  B9   explain/shap_explainer.py       — SHAP for XGBoost
  B10  explain/explanation_output.py   — human-readable "why this fault"

Phase B depends on:
  - A2/A3/A4 outputs (beacon_health.json, baselines.json)
  - Stage 3's latest_prediction.json (already exists)
  - Stage 4's knowledge_base/past_incidents/*.json (already exists)

No new dependencies required. Estimated 10 files.

═══════════════════════════════════════════════════════════════════════════════
END OF PHASE A HANDOFF
Phase A: COMPLETE | Files: 7 | Tests: 100% pass | Ready for Phase B
═══════════════════════════════════════════════════════════════════════════════
