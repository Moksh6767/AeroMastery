# Measured benchmark — 14 October 2026

Executed in this workspace on Windows 11, Python 3.13.4:

```powershell
Set-Location 'D:\SIH 250'
python -m civilian_sim.benchmark --scenarios 100 --sizes 20 100 300
```

**100 paired seeded scenarios per fleet size**, seeds 42–141, three methods, two stages (initial and disrupted replan): **1,800 measured planning calls**. This is not a benchmark of actual human planners. Baselines and multi-start heuristic share the same resource-feasibility model. All calls use 32 uncertainty samples and disable diagnosis. Timing includes candidate construction/evaluation, not dashboard, diagnosis, contingency-cache refresh or network overhead.

## Disrupted replanning results

| Aircraft | Method | Mean task coverage | Mean P1 coverage | Mean latency (ms) | 95% CI of mean latency (ms) |
|---:|---|---:|---:|---:|---:|
| 20 | Input-order greedy | 70.57% | 69.85% | 1.92 | 1.88–1.97 |
| 20 | Priority-first | 75.11% | 99.24% | 2.01 | 1.98–2.03 |
| 20 | Multi-start heuristic | 79.46% | 99.12% | 10.12 | 9.96–10.28 |
| 100 | Input-order greedy | 60.88% | 60.14% | 12.54 | 12.44–12.64 |
| 100 | Priority-first | 64.41% | 99.98% | 13.00 | 12.90–13.11 |
| 100 | Multi-start heuristic | 64.41% | 99.98% | 70.47 | 70.21–70.73 |
| 300 | Input-order greedy | 67.43% | 67.59% | 67.13 | 66.77–67.49 |
| 300 | Priority-first | 72.13% | 100.00% | 68.95 | 68.36–69.53 |
| 300 | Multi-start heuristic | 72.13% | 100.00% | 391.53 | 389.04–394.01 |

The multi-start method improves mean overall coverage for the 20-aircraft sample but **does not improve coverage over priority-first at larger tested sizes**; it is slower at every size. At size 20, its P1 coverage is slightly lower than priority-first. It must not be advertised as uniformly superior. These findings motivate richer search and paired-difference analysis rather than unsupported optimality claims.

The complete local artifacts are:

* `D:\SIH 250\civilian_sim\runtime\benchmarks.json`: raw per-scenario metrics, paired-world/assignment hashes, source hashes, method/settings metadata, and aggregates for both stages. Each metric includes mean, sample variance, normal-approximation 95% CI, p50 and p95.
* `D:\SIH 250\civilian_sim\runtime\benchmarks.svg`: initial/replan scaling chart with confidence bars.

Runtime artifacts are intentionally ignored by Git; regenerate them using the command above. The dashboard links to both artifacts after they exist. This summary preserves the observed results in source control without checking in 2.2 MB of raw output.

Confidence intervals of means use `mean ± 1.96 × sqrt(sample_variance / 100)` and are **not** paired hypothesis tests or guarantees. Monte Carlo robustness has a separate per-plan Wilson interval. Seeds are reused across sizes, and methods/stages share each seeded world. The disruption is an independently selected non-airborne aircraft delayed 120–240 minutes **at time zero**; this benchmark does not measure the complete real-time execution loop. Probabilities, demand and resource limits are synthetic and uncalibrated. Hardware load affects timings; automated validation was also run during this measurement session.

## Other executed checks

* 41 automated tests passed, including HTTP integration, schema rejection, scheduling constraints, confidence statistics, paired benchmark design, hash-chain tampering, and two-reviewer workflows.
* Complete 480-minute default-seed scenario: 16 automatic events, 22 completed relief tasks, paused at horizon; 62-entry audit chain verified.
* Python compilation and browser JavaScript syntax/mocked-DOM checks passed. No visual browser automation or live Ollama/Open-Meteo check was performed.