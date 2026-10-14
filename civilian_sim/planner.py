"""Multi-start constructive heuristic with fixed-plan relaxation diagnostics."""
from collections import defaultdict
from copy import deepcopy
import math
import random
import time

DEFAULT_WEIGHTS = {"coverage": 5., "risk": 2., "disruption": 2., "fuel": 1., "slack": 1.}


def validate_weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(DEFAULT_WEIGHTS):
        raise ValueError("Supply exactly coverage, risk, disruption, fuel and slack")
    if any(type(v) not in (int, float) or not 0 <= v <= 100 or not math.isfinite(v) for v in weights.values()):
        raise ValueError("Weights must be finite numbers from 0 to 100")
    if sum(weights.values()) <= 0:
        raise ValueError("At least one weight must be positive")
    return dict(weights)


class Reservations:
    def __init__(self, world):
        self.world = world
        self.aircraft = {a["id"]: deepcopy(a) for a in world["aircraft"]}
        self.crews = {c["id"]: deepcopy(c) for c in world["crews"]}
        self.bases = {b["id"]: deepcopy(b) for b in world["bases"]}
        self.slots = defaultdict(int)
        # Inventory/fuel and aircraft/crew readiness were committed at dispatch.
        for a in world.get("active", []):
            self.slots[a["base"], "runway", int(a["end"] // 15)] += 1
            for slot in range(int(a["end"] // 15), math.ceil((a["end"] + 30) / 15)):
                self.slots[a["base"], "ground", slot] += 1

    def candidate(self, task, ignore=None):
        base = self.bases[task["base"]]
        blocked = set()
        aircraft = [a for a in self.aircraft.values() if a["base"] == task["base"] and a["kind"] == task["aircraft_kind"]]
        crews = sorted((c for c in self.crews.values() if c["base"] == task["base"] and task["aircraft_kind"] in c["qualifications"]), key=lambda c: c["ready_at"])
        if not aircraft:
            return None, {"No aircraft of the required type at this base"}
        if not crews:
            return None, {"No locally based crew qualified for this aircraft type"}
        for resource, amount in (("supplies", task["supplies"]),):
            if base[resource] < amount and ignore != resource:
                blocked.add(f"Insufficient {resource} remaining across waves")
        if blocked:
            return None, blocked
        first = math.ceil(max(self.world["now"] + 15, task["earliest"]) / 15) * 15
        last = min(task["deadline"], self.world["horizon"]) - task["duration"]
        for start in range(first, math.floor(last) + 1, 15):
            end = start + task["duration"]
            if start - 15 < base["closed_until"] and ignore != "weather":
                blocked.add("Base closure overlaps preparation/departure window")
                continue
            crew = next((c for c in crews if (c["ready_at"] <= start or ignore == "crew_ready") and c["duty_used"] + task["duration"] <= c["duty_limit"]), None)
            if crew is None:
                blocked.add("Qualified crews unavailable after rest/duty constraints")
                continue
            uses = [("runway", start // 15), ("runway", end // 15), ("loading", start // 15 - 1)]
            uses += [("ground", slot) for slot in range(end // 15, math.ceil((end + 30) / 15))]
            if ignore != "capacity" and any(self.slots[base["id"], r, s] >= base[r + "_slots"] for r, s in uses):
                blocked.add("Runway, loading or turnaround capacity occupied")
                continue
            for a in sorted(aircraft, key=lambda a: (-a["serviceability"], a["ready_at"])):
                if a["ready_at"] > start - 15 and ignore != "aircraft_ready":
                    blocked.add("Aircraft maintenance/turnaround extends beyond available window")
                    continue
                refuel = max(0, task["fuel"] + 20 - a["fuel"])
                if refuel > base["fuel_stock"] and ignore != "fuel_stock":
                    blocked.add("Insufficient fuel_stock remaining across waves")
                    continue
                if task["fuel"] + 20 > a["capacity"]:
                    blocked.add("Flight fuel plus reserve exceeds tank capacity")
                    continue
                return {"task_id": task["id"], "aircraft_id": a["id"], "crew_id": crew["id"],
                        "start": start, "end": end, "base": task["base"], "fuel": task["fuel"],
                        "refuel": refuel, "slack": task["deadline"] - end, "uses": uses}, blocked
        return None, blocked or {"No departure slot within task window and planning horizon"}

    def reserve(self, assignment, task):
        a = self.aircraft[assignment["aircraft_id"]]
        c = self.crews[assignment["crew_id"]]
        a["ready_at"] = assignment["end"] + 30
        a["fuel"] += assignment["refuel"] - task["fuel"]
        c["ready_at"] = assignment["end"] + c["rest_minutes"]
        c["duty_used"] += task["duration"]
        self.bases[task["base"]]["supplies"] -= task["supplies"]
        self.bases[task["base"]]["fuel_stock"] -= assignment["refuel"]
        for resource, slot in assignment["uses"]:
            self.slots[task["base"], resource, slot] += 1


def construct(world, order, previous, diagnose=False):
    reservations = Reservations(world)
    assignments, failures = [], []
    for task in order:
        candidate, reasons = reservations.candidate(task)
        if candidate:
            reservations.reserve(candidate, task)
            assignments.append({k: v for k, v in candidate.items() if k != "uses"})
        else:
            advice = []
            if diagnose:
                relaxations = {"aircraft_ready": "Earlier aircraft maintenance/turnaround completion",
                               "crew_ready": "Additional rested, qualified crew (do not waive rest)",
                               "weather": "Base reopening before preparation",
                               "capacity": "Extra runway/loading/ground capacity",
                               "supplies": "Additional relief supplies", "fuel_stock": "Additional base fuel"}
                for key, label in relaxations.items():
                    test, _ = reservations.candidate(task, ignore=key)
                    if test:
                        advice.append(f"{label} alone permits departure at minute {test['start']} with {test['aircraft_id']} / {test['crew_id']}, holding earlier allocations fixed.")
                if not advice:
                    advice.append("No tested single-constraint relaxation suffices; combined conflicts or an expired window remain.")
            failures.append({"task_id": task["id"], "reasons": sorted(reasons), "counterfactuals": advice})
    return {"assignments": assignments, "unscheduled": failures,
            "metrics": metrics(world, assignments, previous), "method": "multi-start heuristic"}


def metrics(world, assignments, previous):
    tasks = {t["id"]: t for t in world["tasks"] if t["status"] == "pending"}
    aircraft = {a["id"]: a for a in world["aircraft"]}
    bases = {b["id"]: b for b in world["bases"]}
    old = {a["task_id"]: (a["aircraft_id"], a["crew_id"], a["start"]) for a in previous if a["task_id"] in tasks}
    new = {a["task_id"]: (a["aircraft_id"], a["crew_id"], a["start"]) for a in assignments}
    p1 = sum(t["priority"] == 1 for t in tasks.values())
    covered_p1 = sum(tasks[a["task_id"]]["priority"] == 1 for a in assignments)
    total_value = sum(4 - t["priority"] for t in tasks.values())
    return {"coverage": len(assignments) / len(tasks) if tasks else 1.,
            "weighted_coverage": sum(4 - tasks[a["task_id"]]["priority"] for a in assignments) / total_value if total_value else 1.,
            "p1_coverage": covered_p1 / p1 if p1 else 1.,
            "risk": sum(1 - aircraft[a["aircraft_id"]]["serviceability"] * (1 - bases[a["base"]]["weather_failure"]) for a in assignments) / len(assignments) if assignments else 0.,
            "disruption": sum(new.get(k) != v for k, v in old.items()) / len(old) if old else 0.,
            "fuel": sum(a["fuel"] for a in assignments),
            "slack": sum(a["slack"] for a in assignments) / len(assignments) if assignments else 0.}


def score(plan, weights, fuel_scale):
    m = plan["metrics"]
    uncertainty = m["risk"]
    if m.get("robustness") is not None:
        uncertainty = (uncertainty + 1 - m["robustness"]) / 2
    return (weights["coverage"] * m["weighted_coverage"] - weights["risk"] * uncertainty
            - weights["disruption"] * m["disruption"] - weights["fuel"] * m["fuel"] / max(1, fuel_scale)
            + weights["slack"] * m["slack"] / 480)


def solve(world, method="solver", weights=None, previous=None, samples=128, diagnose=True):
    started = time.perf_counter()
    weights = validate_weights(DEFAULT_WEIGHTS if weights is None else weights)
    previous = previous or []
    tasks = [t for t in world["tasks"] if t["status"] == "pending"]
    old = {a["task_id"]: a["start"] for a in previous}
    orders = [tasks, sorted(tasks, key=lambda t: (t["priority"], t["deadline"]))]
    if method == "solver":
        orders += [sorted(tasks, key=lambda t: (t["deadline"], t["priority"])),
                   sorted(tasks, key=lambda t: (t["priority"], t["duration"], t["deadline"])),
                   sorted(tasks, key=lambda t: (old.get(t["id"], 10000), t["priority"], t["deadline"])),
                   sorted(tasks, key=lambda t: (t["priority"], -(t["deadline"]-t["earliest"]-t["duration"])))]
    elif method == "manual":
        orders = orders[:1]
    elif method == "priority":
        orders = orders[1:2]
    else:
        raise ValueError("Unknown planning method")
    candidates = [construct(world, order, previous) for order in orders]
    for candidate in candidates:
        candidate["metrics"].update(robustness(world, candidate, min(samples, 32)))
    best_index = max(range(len(candidates)), key=lambda i: score(candidates[i], weights, sum(t["fuel"] for t in tasks)))
    plan = construct(world, orders[best_index], previous, diagnose) if diagnose else candidates[best_index]
    plan["method"] = {"solver": "multi-start heuristic (not an exact solver)", "manual": "manual-style input-order greedy", "priority": "priority-first heuristic"}[method]
    plan["metrics"].update(robustness(world, plan, samples))
    plan["metrics"]["latency_ms"] = (time.perf_counter() - started) * 1000
    return plan


def robustness(world, plan, samples=128):
    if samples < 1:
        return {"robustness": None, "robustness_ci": None, "samples": 0}
    rng = random.Random(world["seed"] + 9181)
    tasks = {t["id"]: t for t in world["tasks"] if t["status"] == "pending"}
    p1 = {t["id"] for t in tasks.values() if t["priority"] == 1}
    selected = {a["task_id"] for a in plan["assignments"]}
    success = 0
    for _ in range(samples):
        # Common random futures across competing plans; base weather is correlated.
        failures = {a["id"]: rng.random() > a["serviceability"] for a in world["aircraft"]}
        delays = {b["id"]: rng.randint(15, 90) if rng.random() < b["weather_failure"] else 0 for b in world["bases"]}
        okay = p1 <= selected
        for assignment in plan["assignments"]:
            if assignment["task_id"] in p1:
                okay = okay and not failures[assignment["aircraft_id"]] and delays[assignment["base"]] <= assignment["slack"]
        success += bool(okay)
    rate = success / samples
    z = 1.96
    center = (rate + z*z/(2*samples))/(1+z*z/samples)
    radius = z*math.sqrt(rate*(1-rate)/samples+z*z/(4*samples*samples))/(1+z*z/samples)
    return {"robustness": rate, "robustness_ci": [max(0, center-radius), min(1, center+radius)], "samples": samples,
            "p1_count": len(p1), "robustness_note": "Synthetic fixed-plan P1 deadline/serviceability check; not a full recourse or delayed-resource simulation. No P1 tasks is vacuously successful."}


def frontier(world, previous=None):
    candidates = []
    for focus in DEFAULT_WEIGHTS:
        weights = {k: (15. if k == focus else 1.) for k in DEFAULT_WEIGHTS}
        plan = solve(world, weights=weights, previous=previous, samples=64, diagnose=False)
        candidates.append({"id": focus, "metrics": plan["metrics"], "weights": weights})
    def vector(p):
        m = p["metrics"]
        return (-m["coverage"], m["risk"], m["disruption"], m["fuel"])
    result, seen = [], set()
    for candidate in candidates:
        v = vector(candidate)
        if v in seen:
            continue
        seen.add(v)
        if not any(all(x <= y for x, y in zip(vector(other), v)) and any(x < y for x, y in zip(vector(other), v)) for other in candidates):
            result.append(candidate)
    return {"plans": result, "note": "Non-dominated sampled heuristic candidates, not a proven global Pareto frontier. Risk is conditional on scheduled flights; inspect coverage too."}