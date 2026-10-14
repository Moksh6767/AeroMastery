# Verification record

Executed locally on 14 October 2026 using CPython 3.12 and Node.js 24:

| Check | Outcome |
|---|---|
| New platform regression suite | 20 passed: seeded data, trust/staleness/conflict blocking, nine hard-constraint cases, interval/capacity checks, infeasible commitments, role enforcement, rejection/stale approvals, persistent restart, append-only audit, WebSocket notifications, read-only copilot, modification workflow, training/inference |
| Original simulator suite | 41 tests passed |
| TypeScript check | Passed |
| Next.js production build | Passed |
| npm dependency audit | Zero reported vulnerabilities after dependency updates |
| Synthetic maintenance training | 1,200 training rows and 400 held-out rows; results in `prediction-metrics.json` |
| Scripted benchmarks | Four presets, seeds 42–45, six aircraft each; all initial solves reported OPTIMAL; results in `benchmark-results.json` |
| Compose configuration | `docker compose --env-file .env.example config --quiet` passed |
| Browser smoke check | Production UI loaded a seeded plan; approver produced version 1; MapLibre bases/routes rendered after worker packaging fix; planner modification superseded the original proposal; rejection preserved version 1 |
| Windows launcher | PowerShell syntax validation passed; full install/start script was not executed as a single command |

Reports describe this local run, not guaranteed performance across machines. Benchmark outputs are exact script-generated JSON. To regenerate, run `python -m relief_platform.cli train` followed by `python -m relief_platform.cli benchmark --scenarios 4` and copy the generated runtime reports if a new checked-in snapshot is desired.

The current dependency combination emits deprecation warnings from Starlette/AnyIO and Joblib/NumPy in the tests; tests pass. Docker containers/PostgreSQL and live Gemini calls were not exercised. No Gemini API key was supplied. No production identity or operational flight validation was performed.

Implementation references: [OR-Tools optional interval and no-overlap API](https://or-tools.github.io/docs/python/classortools_1_1sat_1_1python_1_1cp__model_1_1CpModel.html), [FastAPI SQLModel integration](https://fastapi.tiangolo.com/tutorial/sql-databases/), [MapLibre ES module worker packaging](https://maplibre.org/maplibre-gl-js/docs/).
