"""Simulation clock, dispatch lifecycle, automatic events and reviewed what-ifs."""
from copy import deepcopy
import hmac
import math
import random
import secrets
from threading import RLock

from .model import generate, timestamp, validate_scenario, apply_scenario
from .planner import DEFAULT_WEIGHTS, solve, frontier, validate_weights


class Engine:
    def __init__(self, audit, reviewers, seed=42):
        if len(reviewers) < 2 or len(set(reviewers.values())) != len(reviewers):
            raise ValueError("At least two distinct reviewer credentials are required")
        self.lock = RLock()
        self.audit = audit
        self.reviewers = reviewers
        self.reset(seed)

    def reset(self, seed):
        with self.lock:
            world = generate(seed)
            self.audit.append("reset", {"seed": seed})
            self.world = world
            self.rng = random.Random(seed + 77)
            self.paused, self.speed = True, 1.
            self.version = getattr(self, "version", 0) + 1
            self.weights = dict(DEFAULT_WEIGHTS)
            self.events, self.pending, self.cache = [], {}, {}
            self.next_event, self.next_replan = 30., 15.
            self.plan = solve(self.world, weights=self.weights)
            self._cache()

    def _cache(self):
        self.cache = {}
        active_ids = {a["aircraft_id"] for a in self.world["active"]}
        likely = sorted((a for a in self.world["aircraft"] if a["id"] not in active_ids), key=lambda a: a["serviceability"])[:2]
        for aircraft in likely:
            scenario = {"type": "aircraft_delay", "aircraft_id": aircraft["id"], "minutes": 60}
            alternate = deepcopy(self.world)
            apply_scenario(alternate, scenario)
            self.cache[aircraft["id"]] = {"scenario": scenario, "version": self.version,
                                          "now": self.world["now"], "plan": solve(alternate, weights=self.weights, previous=self.plan["assignments"], samples=64)}

    def replan(self):
        self.plan = solve(self.world, weights=self.weights, previous=self.plan["assignments"])
        self.version += 1
        self._cache()

    def control(self, value):
        with self.lock:
            if not isinstance(value, dict) or not set(value) <= {"paused", "speed"}:
                raise ValueError("Only paused and speed controls are accepted")
            if "paused" in value and type(value["paused"]) is not bool:
                raise ValueError("paused must be boolean")
            speed = value.get("speed", self.speed)
            if type(speed) not in (int, float) or not 1 <= speed <= 60 or not math.isfinite(speed):
                raise ValueError("speed must be between 1 and 60")
            self.audit.append("clock_control", value)
            self.paused = value.get("paused", self.paused)
            self.speed = speed

    def preferences(self, value):
        with self.lock:
            weights = validate_weights(value)
            self.audit.append("preferences", weights)
            self.weights = weights
            self.replan()

    def advance(self, real_seconds):
        with self.lock:
            if self.paused:
                return
            target = min(self.world["horizon"], self.world["now"] + max(0, real_seconds) * self.speed / 60)
            # Process every crossed boundary, independent of wall-clock tick size.
            while self.world["now"] < target:
                now = self.world["now"]
                departures = [a["start"] for a in self.plan["assignments"] if a["start"] > now]
                arrivals = [a["end"] for a in self.world["active"] if a["end"] > now]
                boundary = min([target, self.next_event, self.next_replan] + departures + arrivals)
                self.world["now"] = boundary
                changed = self._lifecycle()
                if boundary >= self.next_event:
                    self._event()
                    self.next_event += 30
                    changed = True
                if changed or boundary >= self.next_replan:
                    self.replan()
                    self.next_replan = (math.floor(boundary / 15) + 1) * 15
            if target >= self.world["horizon"]:
                self.paused = True

    def _lifecycle(self):
        now, changed = self.world["now"], False
        tasks = {t["id"]: t for t in self.world["tasks"]}
        for assignment in list(self.world["active"]):
            if assignment["end"] <= now:
                tasks[assignment["task_id"]]["status"] = "completed"
                self.world["active"].remove(assignment)
                self.audit.append("arrival", assignment)
                changed = True
        for assignment in list(self.plan["assignments"]):
            task = tasks[assignment["task_id"]]
            if assignment["start"] <= now and task["status"] == "pending":
                aircraft = next(a for a in self.world["aircraft"] if a["id"] == assignment["aircraft_id"])
                crew = next(c for c in self.world["crews"] if c["id"] == assignment["crew_id"])
                base = next(b for b in self.world["bases"] if b["id"] == assignment["base"])
                task["status"] = "in_flight"
                aircraft["ready_at"] = assignment["end"] + 30
                aircraft["fuel"] += assignment["refuel"] - assignment["fuel"]
                crew["ready_at"] = assignment["end"] + crew["rest_minutes"]
                crew["duty_used"] += task["duration"]
                base["supplies"] -= task["supplies"]
                base["fuel_stock"] -= assignment["refuel"]
                self.world["active"].append(deepcopy(assignment))
                self.audit.append("dispatch", assignment)
                changed = True
        # Preserve expired pending requests for visible infeasibility diagnosis.
        return changed

    def _event(self):
        event_index = int(self.world["now"] // 30) % 3
        if event_index == 1:
            active = {a["aircraft_id"] for a in self.world["active"]}
            available = [a for a in self.world["aircraft"] if a["id"] not in active]
            if not available:
                return
            aircraft = self.rng.choice(available)
            scenario = {"type": "aircraft_delay", "aircraft_id": aircraft["id"], "minutes": 60}
            description = f"Maintenance fault: {aircraft['id']} unavailable for at least 60 minutes"
        elif event_index == 2:
            base = self.rng.choice(self.world["bases"])
            scenario = {"type": "base_closure", "base": base["id"], "minutes": 30}
            description = f"Synthetic weather: {base['id']} departures suspended for 30 minutes; airborne flights remain committed"
        else:
            scenario = {"type": "relief_request", "base": self.rng.choice(self.world["bases"])["id"], "priority": 1, "duration": 45}
            description = f"New P1 civilian relief request at {scenario['base']}"
        apply_scenario(self.world, scenario)
        event = {"time": timestamp(self.world["now"]), "kind": scenario["type"], "description": description}
        self.events.append(event)
        self.audit.append("synthetic_event", {"event": event, "scenario": scenario})

    def preview(self, scenario):
        with self.lock:
            scenario = validate_scenario(scenario, self.world)
            alternate = deepcopy(self.world)
            apply_scenario(alternate, scenario)
            cached = self.cache.get(scenario.get("aircraft_id"))
            hit = bool(cached and cached["scenario"] == scenario and cached["version"] == self.version and cached["now"] == self.world["now"])
            plan = deepcopy(cached["plan"]) if hit else solve(alternate, weights=self.weights, previous=self.plan["assignments"])
            proposal_id = secrets.token_hex(8)
            proposal = {"id": proposal_id, "scenario": scenario, "description": str(scenario),
                        "approvals": [], "status": "pending", "version": self.version,
                        "now": self.world["now"], "plan": plan}
            self.audit.append("whatif_proposed", {"id": proposal_id, "scenario": scenario, "version": self.version, "cache_hit": hit})
            self.pending[proposal_id] = proposal
            return {"scenario": scenario, "baseline": self.plan["metrics"], "preview": plan["metrics"],
                    "proposal_id": proposal_id, "requires_approval": True, "cache_hit": hit}

    def approve(self, proposal_id, reviewer, token):
        with self.lock:
            if not isinstance(reviewer, str) or not isinstance(token, str) or reviewer not in self.reviewers or not hmac.compare_digest(self.reviewers[reviewer], token):
                raise PermissionError("Invalid reviewer credentials")
            if not isinstance(proposal_id, str) or proposal_id not in self.pending:
                raise ValueError("Unknown proposal")
            proposal = self.pending[proposal_id]
            if proposal["status"] != "pending":
                raise ValueError("Proposal no longer pending")
            if proposal["version"] != self.version or proposal["now"] != self.world["now"]:
                proposal["status"] = "stale"
                raise ValueError("State changed; pause clock and generate a fresh preview")
            if reviewer in proposal["approvals"]:
                raise ValueError("A second distinct reviewer is required")
            self.audit.append("approval", {"id": proposal_id, "reviewer": reviewer})
            proposal["approvals"].append(reviewer)
            if len(proposal["approvals"]) == 2:
                apply_scenario(self.world, proposal["scenario"])
                self.plan = deepcopy(proposal["plan"])
                proposal["status"] = "applied"
                self.version += 1
                self.audit.append("reviewed_scenario_applied", {"id": proposal_id})
                self._cache()

    def state(self):
        with self.lock:
            return deepcopy({"sim_minute": self.world["now"], "sim_time": timestamp(self.world["now"]),
                             "speed": self.speed, "paused": self.paused, "seed": self.world["seed"], "version": self.version,
                             "plan": self.plan, "tasks": self.world["tasks"], "aircraft": self.world["aircraft"],
                             "bases": self.world["bases"], "active": self.world["active"], "events": self.events[-100:],
                             "audit": self.audit.verify(), "weights": self.weights,
                             "pending": [{k: v for k, v in p.items() if k != "plan"} for p in list(self.pending.values())[-50:]],
                             "contingencies": [{"key": k, "description": f"60-minute maintenance delay of {k}", "ready": v["version"] == self.version and v["now"] == self.world["now"], "version": v["version"]} for k, v in self.cache.items()]})

    def frontier(self):
        with self.lock:
            return frontier(self.world, self.plan["assignments"])