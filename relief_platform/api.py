import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import threading
from uuid import uuid4
from fastapi import (
    FastAPI,
    Depends,
    Header,
    HTTPException,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine, select
from .models import (
    Scenario,
    PlanVersion,
    RetaskingProposal,
    AuditEntry,
    ScenarioRequest,
    Disruption,
    Review,
    Chat,
    Base,
    Aircraft,
    Crew,
    ReliefMission,
    MaintenanceRecord,
    WeatherCell,
    AirspaceZone,
)
from .scenarios import scenario, fuse, disrupt
from .optimizer import solve, baseline, diff
from .predictions import predict
from .copilot import answer
from .storage import save_entities, save_plan
from .models import Reason


def create_app(database_url=None, runtime=None, tokens=None):
    runtime = Path(runtime or os.getenv("RELIEF_RUNTIME", "relief_platform/runtime"))
    runtime.mkdir(parents=True, exist_ok=True)
    url = database_url or os.getenv("DATABASE_URL", f"sqlite:///{runtime/'relief.db'}")
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    configured = tokens or {
        r: os.getenv("RELIEF_" + r.upper() + "_TOKEN") or secrets.token_urlsafe(24)
        for r in ("viewer", "planner", "approver")
    }
    if len(set(configured.values())) != 3:
        raise ValueError("Role tokens must be distinct")
    lock = threading.RLock()
    jobs = {}
    clients = set()

    @asynccontextmanager
    async def lifespan(app):
        SQLModel.metadata.create_all(engine)
        if url.startswith("sqlite"):
            with engine.begin() as conn:
                for operation in ("UPDATE", "DELETE"):
                    conn.execute(
                        text(
                            f"CREATE TRIGGER IF NOT EXISTS audit_no_{operation.lower()} BEFORE {operation} ON auditentry BEGIN SELECT RAISE(ABORT, 'append-only audit'); END"
                        )
                    )
        app.state.loop = asyncio.get_running_loop()
        if not tokens:
            print(
                "Local demo role tokens (set environment variables for stable credentials):",
                flush=True,
            )
            for role, token in configured.items():
                print(f"  {role}: {token}", flush=True)
        yield
        engine.dispose()

    app = FastAPI(
        title="Relief Atlas - Synthetic Civilian Decision Support",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def role(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ")
        for name, expected in configured.items():
            if secrets.compare_digest(token, expected):
                return name
        raise HTTPException(401, "Provide a valid demo role bearer token")

    def require(actor, allowed):
        if actor not in allowed:
            raise HTTPException(403, "Role cannot perform this action")

    def get(session, cls, identity):
        row = session.get(cls, identity)
        if row is None:
            raise HTTPException(404, "Record not found")
        return row

    def audit(session, actor, action, before=None, after=None):
        codes = {
            "scenario_loaded": Reason.SCENARIO,
            "fusion_report_refreshed": Reason.CONFLICT,
            "plan_candidate_generated": Reason.GENERATION,
            "plan_approved": Reason.APPROVAL,
            "disruption_proposed": Reason.DISRUPTION,
            "proposal_approved": Reason.APPROVAL,
            "proposal_rejected": Reason.REJECTION,
            "proposal_modified": Reason.MODIFICATION,
        }
        session.add(
            AuditEntry(
                actor=actor,
                action=action,
                reason_codes=[codes[action]],
                before=before or {},
                after=after or {},
            )
        )

    def current(session, sid):
        rows = session.exec(
            select(PlanVersion).where(PlanVersion.scenario_id == sid)
        ).all()
        approved = [r for r in rows if r.approved]
        return (
            max(approved, key=lambda p: p.number)
            if approved
            else max(rows, key=lambda p: p.timestamp, default=None)
        )

    async def publish(message):
        for client in tuple(clients):
            try:
                await asyncio.wait_for(client.send_json(message), 2)
            except Exception:
                clients.discard(client)

    def notify(kind, identity):
        asyncio.run_coroutine_threadsafe(
            publish({"type": kind, "id": identity}), app.state.loop
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "synthetic": True, "scope": "civilian-relief"}

    @app.get("/api/scenarios")
    def scenarios(actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            return {
                "presets": [
                    "baseline",
                    "high-tempo",
                    "degraded-weather",
                    "many-aircraft-down",
                ],
                "scenarios": [
                    {
                        "id": r.id,
                        "preset": r.preset,
                        "seed": r.seed,
                        "revision": r.revision,
                    }
                    for r in s.exec(select(Scenario)).all()
                ],
            }

    @app.post("/api/scenarios", status_code=201)
    def load(body: ScenarioRequest, actor=Depends(role)):
        require(actor, {"planner"})
        world, feeds = scenario(body.seed, body.size, body.preset)
        world, report = fuse(world, feeds)
        row = Scenario(
            id=uuid4().hex,
            preset=body.preset,
            seed=body.seed,
            world=world,
            feeds=feeds,
            fusion=report,
        )
        with lock, Session(engine, expire_on_commit=False) as s:
            s.add(row)
            save_entities(s, row)
            audit(
                s,
                actor,
                "scenario_loaded",
                after={"scenario_id": row.id, "preset": body.preset},
            )
            s.commit()
        notify("state_changed", row.id)
        return {"id": row.id}

    @app.get("/api/scenarios/{sid}/state")
    def state(sid: str, actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            row = get(s, Scenario, sid)
            plan = current(s, sid)
            world = deepcopy(row.world)
            predictions = predict(world, runtime)
            return {
                "scenario": row.model_dump(),
                "plan": plan.model_dump() if plan else None,
                "predictions": predictions,
                "proposals": [
                    r.model_dump()
                    for r in s.exec(
                        select(RetaskingProposal).where(
                            RetaskingProposal.scenario_id == sid
                        )
                    ).all()
                ],
            }

    @app.get("/api/scenarios/{sid}/feeds/{source}")
    def source_feed(sid: str, source: str, actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            return [f for f in get(s, Scenario, sid).feeds if f["source"] == source]

    @app.post("/api/scenarios/{sid}/fuse")
    def fusion(sid: str, actor=Depends(role)):
        require(actor, {"planner"})
        with lock, Session(engine, expire_on_commit=False) as s:
            row = get(s, Scenario, sid)
            # Recompute source diagnostics only. Approved disruption state is authoritative;
            # original baseline feeds must never silently erase it.
            _, row.fusion = fuse(row.world, row.feeds)
            s.add(row)
            audit(s, actor, "fusion_report_refreshed", after={"scenario_id": sid})
            s.commit()
        notify("state_changed", sid)
        return row.fusion

    def run_solver(job_id, sid, actor):
        try:
            with Session(engine, expire_on_commit=False) as s:
                row = get(s, Scenario, sid)
                revision = row.revision
                world = deepcopy(row.world)
                old = current(s, sid)
                previous = old.plan["assignments"] if old else []
            predict(world, runtime)
            plan = solve(world, previous if old else None)
            plan["baseline"] = baseline(world)
            if old:
                plan["previous_metrics"] = old.plan["metrics"]
            with lock, Session(engine, expire_on_commit=False) as s:
                row = get(s, Scenario, sid)
                if row.revision != revision:
                    raise ValueError("Scenario changed while solving; regenerate")
                candidate = PlanVersion(
                    id=job_id, scenario_id=sid, number=0, revision=revision, plan=plan
                )
                s.add(candidate)
                save_plan(s, candidate)
                audit(
                    s,
                    actor,
                    "plan_candidate_generated",
                    after={"plan_id": job_id, "status": plan["status"]},
                )
                s.commit()
            jobs[job_id] = {"status": "complete", "plan_id": job_id}
            notify("solver_complete", job_id)
        except Exception:
            jobs[job_id] = {
                "status": "failed",
                "error": "Solver job failed; retry against the current scenario.",
            }
            notify("solver_failed", job_id)

    @app.post("/api/scenarios/{sid}/plans", status_code=202)
    def generate_plan(sid: str, background: BackgroundTasks, actor=Depends(role)):
        require(actor, {"planner"})
        with Session(engine, expire_on_commit=False) as s:
            get(s, Scenario, sid)
        job = uuid4().hex
        jobs[job] = {"status": "running"}
        background.add_task(run_solver, job, sid, actor)
        notify("solver_started", job)
        return {"job_id": job}

    @app.get("/api/jobs/{job}")
    def job_status(job: str, actor=Depends(role)):
        if job not in jobs:
            raise HTTPException(404, "Job not found (jobs are process-local)")
        return jobs[job]

    @app.get("/api/plans/{pid}")
    def get_plan(pid: str, actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            return get(s, PlanVersion, pid)

    @app.post("/api/plans/{pid}/approve")
    def approve_plan(pid: str, body: Review, actor=Depends(role)):
        require(actor, {"approver"})
        with lock, Session(engine, expire_on_commit=False) as s:
            candidate = get(s, PlanVersion, pid)
            row = get(s, Scenario, candidate.scenario_id)
            if candidate.approved or candidate.revision != row.revision:
                raise HTTPException(409, "Candidate already approved or stale")
            if candidate.plan["status"] not in ("OPTIMAL", "FEASIBLE"):
                raise HTTPException(409, "Solver did not produce a feasible plan")
            old = current(s, row.id)
            candidate.number = (old.number if old and old.approved else 0) + 1
            candidate.approved = True
            # Approval invalidates every proposal based on the prior plan/revision.
            row.revision += 1
            candidate.revision = row.revision
            s.add(row)
            s.add(candidate)
            save_plan(s, candidate)
            audit(
                s,
                actor,
                "plan_approved",
                before={"plan": old.plan if old else {}},
                after={
                    "plan_id": pid,
                    "version": candidate.number,
                    "reason": body.reason,
                    "plan": candidate.plan,
                },
            )
            s.commit()
        notify("plan_approved", pid)
        return {"plan_id": pid, "number": candidate.number}

    @app.post("/api/scenarios/{sid}/disruptions", status_code=201)
    def disruption(sid: str, body: Disruption, actor=Depends(role)):
        require(actor, {"planner"})
        with lock, Session(engine, expire_on_commit=False) as s:
            row = get(s, Scenario, sid)
            old = current(s, sid)
            if not old or not old.approved:
                raise HTTPException(
                    409, "Approve an initial plan before proposing a disruption"
                )
            world = deepcopy(row.world)
            revision = row.revision
            previous = old.plan["assignments"]
            base_id = old.id
        try:
            alternate = disrupt(world, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        predict(alternate, runtime)
        # Preserve only individually feasible unaffected commitments for local repair;
        # a failed fixed strategy remains visibly infeasible, never fabricated.
        eligible = solve(alternate, previous, seconds=1)
        feasible = {
            (a["task_id"], a["aircraft_id"], a["crew_id"], a["start"])
            for a in eligible["assignments"]
        }
        unaffected = [
            a
            for a in previous
            if (a["task_id"], a["aircraft_id"], a["crew_id"], a["start"]) in feasible
        ]
        options = []
        for strategy, stability, fixed in (
            ("local repair", 1500, unaffected),
            (
                "partial window repair",
                700,
                [a for a in unaffected if a["start"] >= body.minutes],
            ),
            ("full re-solve", 0, []),
        ):
            p = solve(alternate, previous, stability=stability, fixed=fixed)
            options.append(
                {
                    "strategy": strategy,
                    "plan": p,
                    "diff": diff(previous, p["assignments"]),
                    "tradeoff": f"Coverage {p['metrics']['weighted_coverage']:.1%}; {p['metrics']['changes_per_event']} assignment changes; {p['status']}",
                }
            )
        options.sort(
            key=lambda o: (
                o["plan"]["status"] in ("OPTIMAL", "FEASIBLE"),
                o["plan"]["metrics"]["weighted_coverage"],
                -o["plan"]["metrics"]["changes_per_event"],
            ),
            reverse=True,
        )
        proposal = RetaskingProposal(
            id=uuid4().hex,
            scenario_id=sid,
            revision=revision,
            base_plan_id=base_id,
            event=body.model_dump(),
            options=options,
        )
        with lock, Session(engine, expire_on_commit=False) as s:
            if get(s, Scenario, sid).revision != revision:
                raise HTTPException(409, "State changed while proposals were solving")
            s.add(proposal)
            audit(
                s,
                actor,
                "disruption_proposed",
                after={"proposal_id": proposal.id, "event": body.model_dump()},
            )
            s.commit()
        notify("new_proposals", proposal.id)
        return proposal

    @app.post("/api/proposals/{pid}/modify", status_code=201)
    def modify_proposal(pid: str, body: Disruption, actor=Depends(role)):
        require(actor, {"planner"})
        with lock, Session(engine, expire_on_commit=False) as s:
            old = get(s, RetaskingProposal, pid)
            sid = old.scenario_id
            if (
                old.status != "pending"
                or old.revision != get(s, Scenario, sid).revision
            ):
                raise HTTPException(
                    409, "Only current pending proposals can be modified"
                )
        new = disruption(sid, body, actor)
        with lock, Session(engine, expire_on_commit=False) as s:
            old = get(s, RetaskingProposal, pid)
            if old.status == "pending":
                old.status = "superseded"
                s.add(old)
                audit(
                    s,
                    actor,
                    "proposal_modified",
                    before={"proposal_id": pid, "event": old.event},
                    after={"proposal_id": new.id, "event": new.event},
                )
                s.commit()
        return new

    @app.post("/api/proposals/{pid}/{decision}")
    def review(pid: str, decision: str, body: Review, actor=Depends(role)):
        require(actor, {"approver"})
        if decision not in ("approve", "reject"):
            raise HTTPException(404, "Unknown review action")
        with lock, Session(engine, expire_on_commit=False) as s:
            proposal = get(s, RetaskingProposal, pid)
            row = get(s, Scenario, proposal.scenario_id)
            old = current(s, row.id)
            if (
                proposal.status != "pending"
                or row.revision != proposal.revision
                or not old
                or old.id != proposal.base_plan_id
            ):
                raise HTTPException(
                    409, "Proposal already reviewed or stale; generate a fresh proposal"
                )
            chosen = proposal.options[body.option]
            if decision == "approve":
                if chosen["plan"]["status"] not in ("OPTIMAL", "FEASIBLE"):
                    raise HTTPException(
                        409, "Cannot approve an infeasible solver result"
                    )
                row.world = disrupt(
                    row.world, Disruption.model_validate(proposal.event)
                )
                row.revision += 1
                new = PlanVersion(
                    id=uuid4().hex,
                    scenario_id=row.id,
                    number=old.number + 1,
                    revision=row.revision,
                    approved=True,
                    plan=chosen["plan"],
                )
                new.plan = {**new.plan, "previous_metrics": old.plan["metrics"]}
                s.add(new)
                s.add(row)
                save_entities(s, row)
                save_plan(s, new, chosen["diff"])
                proposal.status = "approved"
            else:
                proposal.status = "rejected"
            s.add(proposal)
            audit(
                s,
                actor,
                "proposal_" + proposal.status,
                before={"plan_id": old.id, "plan": old.plan},
                after={
                    "proposal_id": pid,
                    "option": body.option,
                    "reason": body.reason,
                    "diff": chosen["diff"],
                    "plan": chosen["plan"] if decision == "approve" else old.plan,
                },
            )
            s.commit()
        notify("review_complete", pid)
        return {"status": proposal.status}

    @app.get("/api/scenarios/{sid}/kpis")
    def kpis(sid: str, actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            get(s, Scenario, sid)
            p = current(s, sid)
            return {
                "plan_id": p.id if p else None,
                "current": p.plan["metrics"] if p else {},
                "baseline": p.plan.get("baseline", {}).get("metrics", {}) if p else {},
                "previous": p.plan.get("previous_metrics", {}) if p else {},
            }

    @app.get("/api/audit")
    def audit_entries(action: str = "", actor_filter: str = "", actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            return [
                a.model_dump()
                for a in s.exec(select(AuditEntry).order_by(AuditEntry.id)).all()
                if (not action or action in a.action)
                and (not actor_filter or actor_filter == a.actor)
            ]

    @app.get("/api/benchmarks")
    def benchmarks(actor=Depends(role)):
        path = runtime / "benchmarks.json"
        if not path.exists():
            raise HTTPException(404, "Run the benchmark script first")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/scenarios/{sid}/copilot")
    def chat(sid: str, body: Chat, actor=Depends(role)):
        with Session(engine, expire_on_commit=False) as s:
            row = get(s, Scenario, sid)
            p = current(s, sid)
            context = {
                "scenario_id": sid,
                "get_plan": p.model_dump() if p else None,
                "get_assignment_reasons": p.plan["assignments"] if p else [],
                "list_conflicts": row.fusion["conflicts"],
                "get_kpis": p.plan["metrics"] if p else {},
                "compare_plans": p.plan.get("previous_metrics", {}) if p else {},
                "get_audit_entries": [
                    a.model_dump() for a in s.exec(select(AuditEntry)).all()
                ][-20:],
            }
        result = answer(body.text, context)

        async def stream():
            yield "data: " + json.dumps(
                {"provider": result["provider"], "references": result["references"]}
            ) + "\n\n"
            for chunk in result["answer"].split(" "):
                yield "data: " + json.dumps({"text": chunk + " "}) + "\n\n"
                await asyncio.sleep(0.005)
            yield "data: " + json.dumps({"done": True}) + "\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        # Authenticate in the first frame, avoiding credentials in URLs/access logs.
        await ws.accept()
        try:
            auth = await asyncio.wait_for(ws.receive_json(), 5)
            if not any(
                secrets.compare_digest(str(auth.get("token", "")), v)
                for v in configured.values()
            ):
                await ws.close(code=1008)
                return
            clients.add(ws)
            await ws.send_json({"type": "connected"})
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
            pass
        finally:
            clients.discard(ws)

    return app


app = create_app()
