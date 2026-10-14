"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { State, PlanVersion, World, Metrics } from "./types";
const ReliefMap = dynamic(() => import("./ReliefMap"), { ssr: false });
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const views = [
  "Plan",
  "Map",
  "Timeline",
  "Sources",
  "Disruptions",
  "Review",
  "Audit",
  "KPIs",
  "Readiness",
];
const pct = (v: number | undefined) =>
  v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
export default function Dashboard() {
  const [token, setToken] = useState(""),
    [role, setRole] = useState("planner"),
    [sid, setSid] = useState(""),
    [state, setState] = useState<State | null>(null),
    [view, setView] = useState("Plan"),
    [preset, setPreset] = useState("baseline"),
    [seed, setSeed] = useState(42),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [connection, setConnection] = useState("Offline"),
    [scenarios, setScenarios] = useState<
      { id: string; preset: string; seed: number }[]
    >([]),
    [candidate, setCandidate] = useState<PlanVersion | null>(null),
    [reason, setReason] = useState("Reviewed synthetic civilian plan"),
    [question, setQuestion] = useState("Summarize the current plan"),
    [chat, setChat] = useState(""),
    [event, setEvent] = useState("aircraft_unserviceable"),
    [target, setTarget] = useState(""),
    [modifying, setModifying] = useState(""),
    [minutes, setMinutes] = useState(90),
    [priority, setPriority] = useState(1),
    [audit, setAudit] = useState<Record<string, unknown>[]>([]),
    [filter, setFilter] = useState(""),
    [benchmarks, setBenchmarks] = useState<unknown>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const api = useCallback(
    async (path: string, body?: unknown) => {
      const response = await fetch(API + path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        const e = await response
          .json()
          .catch(() => ({ detail: "Request failed" }));
        throw new Error(
          typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
        );
      }
      return response.json();
    },
    [token],
  );
  const refresh = useCallback(async () => {
    if (sid && token) setState(await api(`/api/scenarios/${sid}/state`));
  }, [api, sid, token]);
  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (!token) return;
    api("/api/scenarios")
      .then((r) => setScenarios(r.scenarios))
      .catch((e) => setError(e.message));
    refresh().catch((e) => setError(e.message));
  }, [token, api, refresh]);
  useEffect(() => {
    if (!token) return;
    let stopped = false,
      timer: ReturnType<typeof setTimeout>;
    const connect = () => {
      const ws = new WebSocket(API.replace(/^http/, "ws") + "/ws");
      wsRef.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ token }));
      ws.onmessage = () => {
        setConnection("Live");
        refresh().catch((e) => setError(e.message));
      };
      ws.onclose = () => {
        setConnection("Disconnected");
        if (!stopped) timer = setTimeout(connect, 4000);
      };
      ws.onerror = () => setConnection("Disconnected");
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [token, refresh]);
  useEffect(() => {
    setTarget("");
    setModifying("");
  }, [sid]);
  const world = state?.scenario.world;
  const plan = state?.plan?.plan;
  const shown = candidate || state?.plan;
  const m = plan?.metrics;
  const targets = world
    ? event === "aircraft_unserviceable"
      ? world.aircraft
      : event === "weather_closure"
        ? world.bases
        : world.tasks
    : [];
  const load = () =>
    run(async () => {
      const r = await api("/api/scenarios", { preset, seed, size: 9 });
      setCandidate(null);
      setSid(r.id);
      const list = await api("/api/scenarios");
      setScenarios(list.scenarios);
    });
  const generate = () =>
    run(async () => {
      const job = await api(`/api/scenarios/${sid}/plans`, {});
      let result;
      for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        result = await api(`/api/jobs/${job.job_id}`);
        if (result.status !== "running") break;
      }
      if (!result || result.status !== "complete")
        throw new Error(
          result?.error || "Solver still running; check job endpoint",
        );
      setCandidate(await api(`/api/plans/${result.plan_id}`));
      await refresh();
    });
  const ask = () =>
    run(async () => {
      setChat("");
      const response = await fetch(`${API}/api/scenarios/${sid}/copilot`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text: question }),
      });
      if (!response.ok) throw new Error("Copilot request failed");
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        const frames = pending.split("\n\n");
        pending = frames.pop() || "";
        for (const frame of frames) {
          if (frame.startsWith("data: ")) {
            const data = JSON.parse(frame.slice(6));
            if (data.text) setChat((c) => c + data.text);
          }
        }
      }
    });
  const exportAudit = () => {
    const blob = new Blob([JSON.stringify(audit, null, 2)], {
        type: "application/json",
      }),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = "civilian-audit.json";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <>
      <div className="banner">
        SYNTHETIC DATA – CIVILIAN ADVISORY ONLY • NO AVIATION CLEARANCE
      </div>
      <header>
        <div className="toolbar">
          <div style={{ flex: 1 }}>
            <small>DISASTER RESPONSE / RESOURCE PLANNING LAB</small>
            <h1>Relief Atlas</h1>
          </div>
          <span className="pill">{connection}</span>
          <span className="pill">
            {state?.scenario.preset || "No scenario"}
          </span>
          <span className="pill">
            Plan{" "}
            {state?.plan?.approved ? `v${state.plan.number}` : "unapproved"}
          </span>
        </div>
        <div className="toolbar">
          <label>
            Role display
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option>planner</option>
              <option>approver</option>
              <option>viewer</option>
            </select>
          </label>
          <label style={{ flex: 1 }}>
            Role bearer token from backend console
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              placeholder="Credentials stay in this page's memory"
            />
          </label>
          <small>The API enforces the token&apos;s role.</small>
        </div>
      </header>
      <div className="shell">
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <div className="toolbar">
          <label>
            Preset
            <select value={preset} onChange={(e) => setPreset(e.target.value)}>
              {[
                "baseline",
                "high-tempo",
                "degraded-weather",
                "many-aircraft-down",
              ].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            Seed
            <input
              type="number"
              min="0"
              max="2147483647"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </label>
          <button
            disabled={busy || !token || role !== "planner"}
            onClick={load}
          >
            Load scenario
          </button>
          <label>
            Saved scenario
            <select
              value={sid}
              onChange={(e) => {
                setCandidate(null);
                setState(null);
                setSid(e.target.value);
              }}
            >
              <option value="">Select scenario</option>
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.preset} · {s.seed} · {s.id.slice(0, 6)}
                </option>
              ))}
            </select>
          </label>
          <button
            disabled={busy || !sid || role !== "planner"}
            onClick={generate}
          >
            {busy ? "Working…" : "Generate plan"}
          </button>
          <button disabled={!sid || busy} onClick={() => run(refresh)}>
            Refresh
          </button>
        </div>
        <div className="metrics" style={{ marginBottom: 22 }}>
          {[
            ["Coverage", pct(m?.coverage)],
            ["Weighted coverage", pct(m?.weighted_coverage)],
            ["Assets utilized", m?.assets_utilized ?? "—"],
            ["Planning seconds", m?.planning_seconds?.toFixed(3) ?? "—"],
          ].map(([label, value]) => (
            <div className="stat" key={label}>
              <small>{label}</small>
              <div className="value">{value}</div>
            </div>
          ))}
        </div>
        <div className="grid">
          <main>
            <nav aria-label="Dashboard views">
              {views.map((v) => (
                <button
                  className={v === view ? "active" : ""}
                  key={v}
                  onClick={() => setView(v)}
                >
                  {v}
                </button>
              ))}
            </nav>
            {!world ? (
              <div className="card">
                <h2>Start a synthetic civilian scenario</h2>
                <p>
                  Enter a role token, load a fictional scenario, then generate a
                  relief schedule. Planners propose; approvers commit a new plan
                  version.
                </p>
              </div>
            ) : (
              <>
                {view === "Plan" && (
                  <div className="card">
                    <h2>
                      Relief assignments {shown && `· ${shown.plan.status}`}
                    </h2>
                    {candidate && (
                      <p className="notice">
                        Candidate {candidate.id.slice(0, 8)} awaiting human
                        approval. The current approved plan remains displayed in
                        the KPI and map views.
                      </p>
                    )}
                    {shown && !shown.approved && (
                      <div className="toolbar">
                        <input
                          aria-label="Approval reason"
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                        />
                        <button
                          disabled={role !== "approver" || busy}
                          onClick={() =>
                            run(async () => {
                              await api(`/api/plans/${shown.id}/approve`, {
                                option: 0,
                                reason,
                              });
                              setCandidate(null);
                              await refresh();
                            })
                          }
                        >
                          Approve initial / candidate plan
                        </button>
                      </div>
                    )}
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Request</th>
                            <th>Aircraft / crew</th>
                            <th>Window</th>
                            <th>Explanation</th>
                          </tr>
                        </thead>
                        <tbody>
                          {shown?.plan.assignments.map((a) => (
                            <tr key={a.task_id}>
                              <td>
                                {a.task_id}
                                <br />
                                <small>
                                  {
                                    world.tasks.find((t) => t.id === a.task_id)
                                      ?.kind
                                  }
                                </small>
                              </td>
                              <td>
                                {a.aircraft_id}
                                <br />
                                {a.crew_id}
                              </td>
                              <td>
                                {a.start}–{a.end} min
                              </td>
                              <td>
                                <details>
                                  <summary>Why selected</summary>
                                  <p>{a.explanation}</p>
                                  <small>{a.reason_codes.join(", ")}</small>
                                </details>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <h3 style={{ marginTop: 18 }}>Unscheduled requests</h3>
                    {shown?.plan.unscheduled.map((u) => (
                      <p key={u.task_id}>
                        <strong>{u.task_id}</strong> ·{" "}
                        {u.reason_codes.join(", ")}
                        <br />
                        <small>{u.explanation}</small>
                      </p>
                    ))}
                    {!shown && (
                      <p>
                        Generate a plan to inspect assignments and constraints.
                      </p>
                    )}
                  </div>
                )}
                {view === "Map" && (
                  <div className="card">
                    <h2>Fused civilian map</h2>
                    <ReliefMap
                      world={world}
                      assignments={plan?.assignments || []}
                    />
                  </div>
                )}
                {view === "Timeline" && (
                  <div className="card">
                    <h2>Aircraft and crew timeline</h2>
                    <small>
                      0–480 simulation minutes; blocks show flight time. Crew
                      rest and aircraft turnaround are enforced by the solver.
                    </small>
                    {[...world.aircraft, ...world.crews].map((r) => (
                      <div className="timeline-row" key={r.id}>
                        <span className="timeline-label">{r.id}</span>
                        <div className="track">
                          {plan?.assignments
                            .filter(
                              (a) =>
                                a.aircraft_id === r.id || a.crew_id === r.id,
                            )
                            .map((a) => (
                              <div
                                className="flight"
                                key={a.task_id}
                                title={`${a.task_id}: ${a.start}–${a.end}`}
                                style={{
                                  left: `${(a.start / 480) * 100}%`,
                                  width: `${((a.end - a.start) / 480) * 100}%`,
                                }}
                              >
                                {a.task_id}
                              </div>
                            ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {view === "Sources" && (
                  <div className="card">
                    <h2>Source fusion and provenance</h2>
                    <p>
                      Freshness is measured against the fixed simulation epoch,
                      not today&apos;s weather.
                    </p>
                    <button
                      disabled={busy || role !== "planner"}
                      onClick={() =>
                        run(async () => {
                          await api(`/api/scenarios/${sid}/fuse`, {});
                          await refresh();
                        })
                      }
                    >
                      Refresh fusion report
                    </button>
                    {Object.entries(state!.scenario.fusion.sources).map(
                      ([name, s]) => (
                        <div className="source" key={name}>
                          <strong>{name}</strong> ·{" "}
                          {s.stale ? "STALE" : "fresh"} ·{" "}
                          {s.age_minutes.toFixed(0)} min old · confidence decay{" "}
                          {pct(s.freshness)}
                        </div>
                      ),
                    )}
                    <h3 style={{ marginTop: 20 }}>Conflicts</h3>
                    {state!.scenario.fusion.conflicts.map((c, i) => (
                      <p key={i}>
                        {c.record_id} / {c.field}: {c.resolution} ·{" "}
                        {c.chosen_source || "human review required"}
                      </p>
                    ))}
                    <details>
                      <summary>Aircraft field provenance</summary>
                      <pre>
                        {JSON.stringify(
                          world.aircraft.map((a) => ({
                            id: a.id,
                            provenance: a.field_provenance,
                          })),
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </div>
                )}
                {view === "Disruptions" && (
                  <div className="card">
                    <h2>Propose a civilian disruption</h2>
                    {modifying && (
                      <p className="notice">
                        Modifying proposal {modifying.slice(0, 8)}. Generating
                        alternatives supersedes the prior proposal and requires
                        a fresh review.
                      </p>
                    )}
                    <p>
                      Changes produce ranked alternatives. Scenario state
                      changes only after approver review.
                    </p>
                    <div className="toolbar">
                      <label>
                        Type
                        <select
                          value={event}
                          onChange={(e) => {
                            setEvent(e.target.value);
                            setTarget("");
                          }}
                        >
                          {[
                            "aircraft_unserviceable",
                            "weather_closure",
                            "airspace_closure",
                            "priority_change",
                          ].map((e) => (
                            <option key={e}>{e}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Target
                        <select
                          value={target}
                          onChange={(e) => setTarget(e.target.value)}
                        >
                          <option value="">Select</option>
                          {targets.map((t) => (
                            <option key={t.id}>{t.id}</option>
                          ))}
                        </select>
                      </label>
                      {event === "priority_change" ? (
                        <label>
                          Priority
                          <input
                            type="number"
                            min="1"
                            max="3"
                            value={priority}
                            onChange={(e) =>
                              setPriority(Number(e.target.value))
                            }
                          />
                        </label>
                      ) : (
                        <label>
                          Closed / unavailable until minute
                          <input
                            type="number"
                            min="15"
                            max="480"
                            value={minutes}
                            onChange={(e) => setMinutes(Number(e.target.value))}
                          />
                        </label>
                      )}
                      <button
                        disabled={busy || !target || role !== "planner"}
                        onClick={() =>
                          run(async () => {
                            await api(
                              modifying
                                ? `/api/proposals/${modifying}/modify`
                                : `/api/scenarios/${sid}/disruptions`,
                              {
                                type: event,
                                target,
                                minutes,
                                priority,
                              },
                            );
                            setModifying("");
                            await refresh();
                            setView("Review");
                          })
                        }
                      >
                        Generate alternatives
                      </button>
                    </div>
                  </div>
                )}
                {view === "Review" && (
                  <div className="card">
                    <h2>Human retasking review</h2>
                    <label>
                      Review reason
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </label>
                    {!state!.proposals.length && (
                      <p>No disruptions proposed.</p>
                    )}
                    {state!.proposals
                      .slice()
                      .reverse()
                      .map((p) => (
                        <section className="proposal" key={p.id}>
                          <h3>
                            {p.id.slice(0, 8)} · {p.status}
                            {p.revision !== state!.scenario.revision
                              ? " · stale"
                              : ""}
                          </h3>
                          <button
                            disabled={
                              busy ||
                              role !== "planner" ||
                              p.status !== "pending" ||
                              p.revision !== state!.scenario.revision
                            }
                            onClick={() => {
                              setModifying(p.id);
                              setEvent(p.event.type);
                              setTarget(p.event.target);
                              setMinutes(p.event.minutes);
                              setPriority(p.event.priority);
                              setView("Disruptions");
                            }}
                          >
                            Modify disruption and regenerate
                          </button>
                          {p.options.map((o, i) => (
                            <div className="proposal" key={i}>
                              <strong>
                                #{i + 1} {o.strategy}
                              </strong>
                              <p>{o.tradeoff}</p>
                              <details>
                                <summary>
                                  Plan diff and downstream resource changes
                                </summary>
                                <pre>{JSON.stringify(o.diff, null, 2)}</pre>
                              </details>
                              <div
                                className="toolbar"
                                style={{ marginTop: 14 }}
                              >
                                {["approve", "reject"].map((d) => (
                                  <button
                                    key={d}
                                    disabled={
                                      busy ||
                                      role !== "approver" ||
                                      p.status !== "pending" ||
                                      p.revision !== state!.scenario.revision
                                    }
                                    onClick={() =>
                                      run(async () => {
                                        await api(
                                          `/api/proposals/${p.id}/${d}`,
                                          { option: i, reason },
                                        );
                                        setCandidate(null);
                                        await refresh();
                                      })
                                    }
                                  >
                                    {d === "approve"
                                      ? "Approve this option"
                                      : "Reject proposal"}
                                  </button>
                                ))}
                              </div>
                            </div>
                          ))}
                        </section>
                      ))}
                  </div>
                )}
                {view === "Audit" && (
                  <div className="card">
                    <h2>Append-only audit</h2>
                    <div className="toolbar">
                      <input
                        aria-label="Action filter"
                        placeholder="Filter action"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      />
                      <button
                        onClick={() =>
                          run(async () =>
                            setAudit(
                              await api(
                                "/api/audit?action=" +
                                  encodeURIComponent(filter),
                              ),
                            ),
                          )
                        }
                      >
                        Load audit
                      </button>
                      <button disabled={!audit.length} onClick={exportAudit}>
                        Export JSON
                      </button>
                    </div>
                    {audit.map((a) => (
                      <details key={String(a.id)}>
                        <summary>
                          #{String(a.id)} · {String(a.actor)} ·{" "}
                          {String(a.action)}
                        </summary>
                        <pre>{JSON.stringify(a, null, 2)}</pre>
                      </details>
                    ))}
                  </div>
                )}
                {view === "KPIs" && (
                  <div className="card">
                    <h2>Measured KPI comparison</h2>
                    {[
                      ["Current", plan?.metrics],
                      ["Priority greedy", plan?.baseline?.metrics],
                      ["Previous", plan?.previous_metrics],
                    ].map(([name, metric]) => {
                      const q = metric as Metrics | undefined;
                      return (
                        <div key={String(name)}>
                          <div className="comparison">
                            <span style={{ width: 120 }}>{String(name)}</span>
                            <div
                              className="bar"
                              style={{
                                width: `${(q?.weighted_coverage || 0) * 55}%`,
                              }}
                            />
                            <small>{pct(q?.weighted_coverage)}</small>
                          </div>
                          {q && (
                            <p className="muted">
                              {q.planning_seconds?.toFixed(3)} s ·{" "}
                              {q.assets_utilized} aircraft ·{" "}
                              {q.crew_duty_violations} duty violations ·{" "}
                              {q.changes_per_event} changes
                            </p>
                          )}
                        </div>
                      );
                    })}
                    <p>
                      <small>
                        Bars show priority-weighted coverage. Timings are
                        measured on this server. A heuristic baseline is not a
                        human performance study.
                      </small>
                    </p>
                    <button
                      onClick={() =>
                        run(async () =>
                          setBenchmarks(await api("/api/benchmarks")),
                        )
                      }
                    >
                      Load scripted benchmarks
                    </button>
                    {benchmarks !== null && (
                      <pre>{JSON.stringify(benchmarks, null, 2)}</pre>
                    )}
                  </div>
                )}
                {view === "Readiness" && (
                  <div className="card">
                    <h2>Synthetic readiness and weather risk</h2>
                    <p>
                      Models learn generated labels; these values do not
                      establish real aircraft safety.
                    </p>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>Aircraft</th>
                            <th>Near-term unavailable risk</th>
                            <th>Model</th>
                          </tr>
                        </thead>
                        <tbody>
                          {state!.predictions.aircraft.map((p) => (
                            <tr key={p.aircraft_id}>
                              <td>{p.aircraft_id}</td>
                              <td>{pct(p.unserviceability_probability)}</td>
                              <td>{p.model}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {state!.predictions.weather.map((w) => (
                      <p key={w.base_id}>
                        {w.base_id}: synthetic weather risk {pct(w.risk)}
                      </p>
                    ))}
                  </div>
                )}
              </>
            )}
          </main>
          <aside className="card copilot">
            <small>READ-ONLY ASSISTANT</small>
            <h2>Relief copilot</h2>
            <p className="muted">
              Ask about coverage, assignment reasons, source conflicts, or audit
              records.
            </p>
            <label>
              Question
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                maxLength={2000}
              />
            </label>
            <button disabled={busy || !sid || !token} onClick={ask}>
              Ask copilot
            </button>
            <div className="answer" aria-live="polite">
              {chat ||
                "Answers cite the current synthetic records. Offline summaries are available without an API key."}
            </div>
            <hr style={{ margin: "20px 0", borderColor: "#304758" }} />
            <small>
              {state
                ? `Scenario ${state.scenario.id.slice(0, 8)} · revision ${state.scenario.revision}`
                : "No scenario loaded"}
              <br />
              No weapon, targeting, or combat functions.
            </small>
          </aside>
        </div>
      </div>
    </>
  );
}
