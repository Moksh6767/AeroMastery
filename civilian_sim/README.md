# Civilian relief-flight simulator

Standalone, offline-first **synthetic civilian disaster-response demonstration**. It does not read or change any of the military CSVs, database, or generator in the repository root. No combat allocation, weapons, hostile tracks, or operational aviation clearance is supported.

## Run on Windows (Python 3.13 tested; no pip packages)

```powershell
Set-Location 'D:\SIH 250'
python -m civilian_sim.server
```

Open **http://127.0.0.1:8765**. The server starts paused. Press play and choose 1–60× speed. At 60×, one real second equals one simulated minute. Automatic events arrive every 30 simulated minutes; periodic planning runs every 15 simulated minutes and after dispatch, arrival, or disruption. The scenario ends at minute 480. Reset regenerates a deterministic seed; it does not erase the audit history.

The console prints separate `reviewer-a` and `reviewer-b` tokens. Pause, create a what-if preview, then approve with both identities/tokens. Any intervening clock/state/preference change makes the proposal stale. For repeatable local development, set **different** `CIVILIAN_REVIEWER_A` and `CIVILIAN_REVIEWER_B` environment variables before launch. Never publish tokens. This local demo does not prove two different humans are present; production identity/SSO, roles and independently controlled credentials are not implemented.

All manual what-if changes require two approvals; autonomous scenario events and baseline heuristic scheduling are pre-authorized simulation behavior, not a real dispatch approval workflow. Applied previews use the reviewed stored plan, not a new unreviewed solve. The UI only recommends/records synthetic allocations.

## Implemented capabilities

* Deterministic relief deliveries, medical evacuations and rescue transport at three fictional bases.
* Simulated clock with pause/speed control; maintenance faults, temporary weather departure closures and new urgent requests; immutable airborne commitments and dispatch/arrival lifecycle.
* Multi-start constructive scheduling compared with input-order greedy and priority-first baselines. Adjustable coverage/risk/disruption/fuel/slack preferences, sampled non-dominated candidate frontier.
* Monte Carlo aircraft serviceability failures and correlated base weather delays. Common seeded futures compare candidate plans fairly; candidate robustness contributes to the risk preference, alongside average serviceability risk. Displayed robustness includes a Wilson 95% binomial interval.
* Explicit unscheduled reasons and tested single-constraint counterfactuals. A successful relaxation is sufficient **holding earlier allocations fixed**; it is not a globally minimal conflict set. Crew advice requests an additional rested qualified crew; it never authorizes waiving rest rules.
* Qualified crew readiness/rest/duty limits, aircraft readiness/turnaround/tank reserve, runway departure/arrival slots, loading/refuelling slots, ground turnaround slots, multi-base supplies and fuel stock carried across waves.
* Cached 60-minute maintenance-delay alternatives for the two least serviceable non-airborne aircraft. Exact same-time/version what-if previews hit the cache; reviewed application reuses the result. Time/state changes invalidate it. Autonomous clock events currently re-solve rather than promising zero-latency contingency execution.
* Hash-chain JSONL audit, verify-integrity action, proposal approvals, structured civilian dispatch JSON export.
* Offline deterministic command parser; optional schema-constrained local Ollama; explicit opt-in Open-Meteo preview mapped to a fictional base.

## Important modeling limits

This is a runnable prototype, **not a certified planner, exact optimizer, CP-SAT model or trained predictive system**. Predictions are declared synthetic probabilities. A task is a round trip to its origin base; there are no inter-base repositioning flights, airspace routing, in-air refuelling, tankers, or real flight performance models. Crew and turnaround intervals are simplified demo assumptions, not aviation regulations. Capacity uses 15-minute buckets; loading/refuelling takes one preparation bucket, aircraft turnaround takes 30 minutes, and synthetic crew rest takes 60 minutes after each task. Aircraft start with 180 fuel units and must retain 20; base fuel stock is charged only for actual refuelling. Supplies are consumed on dispatch.

Robustness answers: “What fraction of sampled futures preserve **all currently pending P1 tasks' aircraft serviceability and task deadlines**, with base delay absorbed by each task's slack?” If any P1 is unscheduled it is zero; if there are no pending P1 tasks it is vacuously one. It does **not** propagate delayed flight times into downstream resource conflicts, simulate full recovery/replanning, or score completed/airborne tasks. Weather samples are correlated within each base, while aircraft failures are independent and persist for the sampled horizon. The displayed confidence interval measures Monte Carlo sampling error, not model accuracy. Default seed 42 may legitimately show 0% robustness when some P1 requests are uncovered—no success numbers are fabricated.

The frontier is non-dominated **among sampled heuristic candidates**, not globally complete. Risk is the mean expected failure probability among scheduled flights; fewer scheduled flights can misleadingly lower risk, so always inspect coverage. Metrics concern currently pending tasks, while the task list retains completed/in-flight status.

Weather closures stop future departures, not arrivals already committed; no emergency airborne recovery is modeled. Simulation events, schedules and uncertainty are reproducible for a fixed seed and controls; audit wall timestamps and measured latencies naturally differ.

## Copilot (offline by default)

The default parser accepts exactly:

```text
delay CV-001 60 minutes
close Harbor 30 minutes
add relief Harbor priority 1 duration 45 minutes
```

Optional flexible natural language requires a separately installed Ollama and downloaded model, for example `ollama pull qwen2.5:3b`. Set `CIVILIAN_OLLAMA_MODEL` to another installed compatible model if needed. The integration calls only `http://127.0.0.1:11434/api/chat`, requests schema-constrained JSON, and validates every field again before the planner runs. The model cannot select assignments or approve plans. An unavailable/malformed model response reports an error without changing the world. A quantized 3B model may fit on a 4GB GPU, but VRAM/context/runtime overhead vary; CPU fallback may be necessary. Model availability and GPU performance were not verified here. Gemini/cloud fallback is deliberately not configured; no scenario data is sent to a cloud LLM.

Open-Meteo is a separate opt-in network action with explicit coordinates. It returns current weather and hourly forecast with attribution. It is a **preview only**: the simulation date is fixed, and present-day forecasts must not silently become synthetic flight-safety judgments. No METAR/TAF ingest or PDF/official tasking-order export is claimed.

## Data export, tests and reproducible benchmark

```powershell
Set-Location 'D:\SIH 250'
python -m civilian_sim.generate_data --seed 42 --size 20
python -m unittest discover -s 'D:\SIH 250\civilian_sim' -t 'D:\SIH 250' -p 'test_*.py' -v
python -m civilian_sim.benchmark --scenarios 100 --sizes 20 100 300
```

Civilian CSV exports and scenario snapshots go to `D:\SIH 250\civilian_sim\runtime\data`. They document generated inputs; the app currently regenerates by seed rather than importing arbitrary CSVs. Benchmark outputs are under `D:\SIH 250\civilian_sim\runtime`; inspect the script/report metadata for sample counts, seeds, units and confidence-interval assumptions. The benchmark measures both initial planning and disrupted replanning, with equal evaluation settings for the three methods. Latency depends on hardware and background load; do not compare it with published external numbers as if environments were identical.

## Security, durability and scope

Server binds to loopback, checks Host/Origin, limits request size, serves only fixed routes and rejects nonfinite parameters. Reviewer tokens are never returned through the API or written into audit payloads. Local users can still control this demo; it is not a multi-user production service.

The audit detects modified entries and internal deletion/reordering. A locally stored hash chain alone **cannot detect removal of a suffix or full-chain replacement** without an external trusted head/checkpoint. The exported dispatch includes the current audit head for independent retention, but no remote anchoring/signing is implemented. Audit writes flush Python buffers; there is no cross-process locking, fsync transaction guarantee, encrypted storage or transactional world replay. Do not run multiple server instances against one audit path. A damaged audit prevents startup/extension and pauses clock execution on errors. Reset/app restart does not restore operational state from the log.

Runtime files are ignored by Git. Existing repository-root files remain untouched.