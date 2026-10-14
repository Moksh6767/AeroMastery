"""Discrete 15-minute civilian scheduling; all constraints are demo assumptions."""

from collections import defaultdict
from time import perf_counter
from ortools.sat.python import cp_model
from shapely.geometry import LineString, Polygon
from civilian_sim.planner import construct, metrics
from .models import Reason


def diff(before, after):
    old = {a["task_id"]: a for a in before}
    new = {a["task_id"]: a for a in after}
    signature = lambda a: (a["aircraft_id"], a["crew_id"], a["start"], a["end"])
    return {
        "added": [new[k] for k in new.keys() - old.keys()],
        "removed": [old[k] for k in old.keys() - new.keys()],
        "changed": [
            {"before": old[k], "after": new[k]}
            for k in old.keys() & new.keys()
            if signature(old[k]) != signature(new[k])
        ],
    }


def solve(world, previous=None, stability=250, seconds=3, fixed=()):
    begun = perf_counter()
    replanning = previous is not None
    previous = previous or ()
    model = cp_model.CpModel()
    aircraft_intervals, crew_intervals, slots, duties, supply, fuel = (
        defaultdict(list) for _ in range(6)
    )
    candidates, by_task, rejected = [], defaultdict(list), defaultdict(set)
    old = {a["task_id"]: a for a in previous}
    bases = {b["id"]: b for b in world["bases"]}
    for task in world["tasks"]:
        if task["status"] != "pending":
            continue
        base = bases[task["base"]]
        route = LineString([(base["longitude"], base["latitude"]), task["destination"]])
        for a in world["aircraft"]:
            if a["base"] != task["base"] or a["kind"] != task["aircraft_kind"]:
                continue
            for c in world["crews"]:
                if c["base"] != task["base"] or a["kind"] not in c["qualifications"]:
                    rejected[task["id"]].add(Reason.QUALIFICATION)
                    continue
                if c["duty_used"] + task["duration"] > c["duty_limit"]:
                    rejected[task["id"]].add(Reason.DUTY)
                    continue
                if any(r.get("fusion_blocked") for r in (a, c, base, task)):
                    rejected[task["id"]].add(Reason.CONFLICT)
                    continue
                for start in range(15, world["horizon"] - task["duration"] + 1, 15):
                    end = start + task["duration"]
                    reasons = set()
                    if start < task["earliest"] or end > task["deadline"]:
                        reasons.add(Reason.WINDOW)
                    if a["ready_at"] > start - 15:
                        reasons.add(Reason.READINESS)
                    if c["ready_at"] > start:
                        reasons.add(Reason.DUTY)
                    if base["closed_until"] > start - 15:
                        reasons.add(Reason.WEATHER)
                    if task["airspace_closed_until"] > start or any(
                        z["until"] > start and route.intersects(Polygon(z["polygon"]))
                        for z in world["zones"]
                    ):
                        reasons.add(Reason.AIRSPACE)
                    if task["fuel"] + 20 > a["capacity"]:
                        reasons.add(Reason.FUEL)
                    if reasons:
                        rejected[task["id"]].update(reasons)
                        continue
                    v = model.new_bool_var(f"choice-{len(candidates)}")
                    assignment = {
                        "task_id": task["id"],
                        "aircraft_id": a["id"],
                        "crew_id": c["id"],
                        "start": start,
                        "end": end,
                        "base": task["base"],
                        "fuel": task["fuel"],
                        "refuel": task["fuel"],
                        "slack": task["deadline"] - end,
                    }
                    candidates.append((v, assignment, task, a, base))
                    by_task[task["id"]].append(v)
                    aircraft_intervals[a["id"]].append(
                        model.new_optional_fixed_size_interval_var(
                            start - 15, task["duration"] + 45, v, f"a-{len(candidates)}"
                        )
                    )
                    crew_intervals[c["id"]].append(
                        model.new_optional_fixed_size_interval_var(
                            start,
                            task["duration"] + c["rest_minutes"],
                            v,
                            f"c-{len(candidates)}",
                        )
                    )
                    duties[c["id"]].append(task["duration"] * v)
                    supply[base["id"]].append(task["supplies"] * v)
                    # Conservative full flight-fuel charge; aircraft start with sufficient reserve.
                    fuel[base["id"]].append(round(task["fuel"] * 10) * v)
                    uses = [
                        ("runway", start // 15),
                        ("runway", end // 15),
                        ("loading", start // 15 - 1),
                    ]
                    uses += [("ground", s) for s in range(end // 15, (end + 30) // 15)]
                    for resource, slot in uses:
                        slots[base["id"], resource, slot].append(v)
    for variables in by_task.values():
        model.add(sum(variables) <= 1)
    for intervals in list(aircraft_intervals.values()) + list(crew_intervals.values()):
        model.add_no_overlap(intervals)
    for c in world["crews"]:
        model.add(sum(duties[c["id"]]) <= c["duty_limit"] - c["duty_used"])
    for base in world["bases"]:
        model.add(sum(supply[base["id"]]) <= base["supplies"])
        model.add(sum(fuel[base["id"]]) <= round(base["fuel_stock"] * 10))
    for (base, resource, slot), variables in slots.items():
        model.add(sum(variables) <= bases[base][resource + "_slots"])
    for assignment in fixed:
        matching = [
            v
            for v, a, *_ in candidates
            if all(
                a[k] == assignment[k]
                for k in ("task_id", "aircraft_id", "crew_id", "start")
            )
        ]
        model.add(sum(matching) == 1)
    objective = []
    for v, a, t, ac, b in candidates:
        stable = t["id"] in old and all(
            a[k] == old[t["id"]][k] for k in ("aircraft_id", "crew_id", "start")
        )
        risk = 1 - ac["serviceability"] * (1 - b["weather_failure"])
        objective.append(
            (
                10000 * (4 - t["priority"])
                - round(1500 * risk)
                - round(t["fuel"])
                - a["start"]
                + (stability if stable else 0)
            )
            * v
        )
    # Minimize maximum crew flying time as a fairness tie-break.
    maximum = model.new_int_var(0, 480, "maximum_crew_load")
    for c in world["crews"]:
        model.add(maximum >= sum(duties[c["id"]]) + c["duty_used"])
    model.maximize(sum(objective) - maximum)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = world["seed"]
    status = solver.solve(model)
    assignments = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for v, a, *_ in candidates:
            if solver.value(v):
                stable = (
                    stability > 0
                    and a["task_id"] in old
                    and all(
                        a[k] == old[a["task_id"]][k]
                        for k in ("aircraft_id", "crew_id", "start")
                    )
                )
                a["reason_codes"] = [Reason.PRIORITY, Reason.RISK] + (
                    [Reason.STABILITY] if stable else []
                )
                a["explanation"] = (
                    "Selected jointly with other relief flights to reward priority coverage, reduce synthetic risk and balance crew load. Hard resource constraints passed."
                )
                assignments.append(a)
    assignments.sort(key=lambda a: (a["start"], a["task_id"]))
    selected = {a["task_id"] for a in assignments}
    unscheduled = [
        {
            "task_id": t["id"],
            "reason_codes": (
                sorted(rejected[t["id"]]) if not by_task[t["id"]] else [Reason.CAPACITY]
            ),
            "explanation": (
                "No feasible candidate."
                if not by_task[t["id"]]
                else "Feasible candidates exist but the joint resource allocation did not select this request."
            ),
        }
        for t in world["tasks"]
        if t["id"] not in selected and t["status"] == "pending"
    ]
    m = metrics(world, assignments, previous)
    m.update(
        planning_seconds=perf_counter() - begun,
        assets_utilized=len({a["aircraft_id"] for a in assignments}),
        crew_duty_violations=0,
        changes_per_event=(
            sum(len(x) for x in diff(previous, assignments).values())
            if replanning
            else 0
        ),
    )
    return {
        "method": "CP-SAT",
        "status": solver.status_name(status),
        "assignments": assignments,
        "unscheduled": unscheduled,
        "metrics": m,
        "candidate_count": len(candidates),
        "objective": solver.objective_value if assignments else None,
        "diagnosis": (
            "Fixed commitments may be infeasible"
            if status == cp_model.INFEASIBLE
            else "Unscheduled reasons distinguish prefilter failures from contention; they are not a minimal unsatisfiable core."
        ),
    }


def baseline(world):
    # Use the same candidate feasibility model; greedily lock one request at a time.
    fixed = []
    start = perf_counter()
    for t in sorted(
        world["tasks"], key=lambda t: (t["priority"], t["deadline"], t["id"])
    ):
        subset = dict(
            world,
            tasks=[
                x
                for x in world["tasks"]
                if x["id"] in {a["task_id"] for a in fixed} | {t["id"]}
            ],
        )
        p = solve(subset, fixed=fixed, seconds=0.3)
        if p["status"] in ("OPTIMAL", "FEASIBLE"):
            fixed = p["assignments"]
    m = metrics(world, fixed, ())
    m.update(
        planning_seconds=perf_counter() - start,
        assets_utilized=len({a["aircraft_id"] for a in fixed}),
        crew_duty_violations=0,
        changes_per_event=0,
    )
    return {"method": "priority-ordered greedy", "assignments": fixed, "metrics": m}
