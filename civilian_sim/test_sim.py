"""Standalone stdlib regression tests; all audit writes stay in temporary folders."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .audit import AuditLog
from .engine import Engine
from .model import apply_scenario, generate, validate_scenario
from .planner import (DEFAULT_WEIGHTS, Reservations, construct, frontier,
                      robustness, solve, validate_weights)


def fixture(resources=1):
    world = generate(17, 3)
    world["bases"] = [world["bases"][0]]
    world["bases"][0].update(runway_slots=10, loading_slots=10,
                             ground_slots=10, weather_failure=0)
    aircraft, crew = world["aircraft"][0], world["crews"][0]
    aircraft.update(ready_at=0, serviceability=1)
    crew.update(ready_at=0)
    world["aircraft"] = [dict(aircraft, id=f"A-{i}") for i in range(resources)]
    world["crews"] = [dict(crew, id=f"C-{i}") for i in range(resources)]
    world["tasks"] = [task()]
    return world


def task(identifier="T-1", **changes):
    value = dict(id=identifier, kind="relief delivery", priority=1, base="Harbor",
                 aircraft_kind="transport", earliest=15, deadline=240,
                 duration=30, supplies=5, fuel=24, status="pending")
    value.update(changes)
    return value


def stable(value):
    """Elapsed compute time is intentionally not a deterministic output."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k != "latency_ms"}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


class ModelTests(unittest.TestCase):
    def test_seed_repeatability_and_isolation(self):
        self.assertEqual(generate(21), generate(21))
        self.assertNotEqual(generate(21), generate(22))
        first = generate(21)
        first["aircraft"][0]["fuel"] = -1
        self.assertGreater(generate(21)["aircraft"][0]["fuel"], 0)

    def test_seed_and_size_validation(self):
        for seed in (True, 1.5, float("nan"), -1, 2**31, "42"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                generate(seed)
        for size in (True, 2, 301, 3.0):
            with self.subTest(size=size), self.assertRaises(ValueError):
                generate(size=size)

    def test_scenario_validation(self):
        world = fixture()
        valid = [dict(type="aircraft_delay", aircraft_id="A-0", minutes=60),
                 dict(type="base_closure", base="Harbor", minutes=30),
                 dict(type="relief_request", base="Harbor", priority=1, duration=45)]
        for scenario in valid:
            result = validate_scenario(scenario, world)
            self.assertEqual(result, scenario)
            self.assertIsNot(result, scenario)
            for field in ("minutes", "priority", "duration"):
                if field in scenario:
                    for bad in (True, False, float("nan"), float("inf"), -float("inf"), "30", -1, 1000):
                        with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                            validate_scenario(dict(scenario, **{field: bad}), world)
            for invalid in (dict(scenario, extra=1), {k: v for k, v in scenario.items() if k != "type"}):
                with self.assertRaises(ValueError):
                    validate_scenario(invalid, world)
        for invalid in (None, [], {}, {"type": "unknown"}, dict(valid[0], aircraft_id="UNKNOWN"),
                        dict(valid[1], base="UNKNOWN"), dict(valid[0], type=True)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_scenario(invalid, world)

    def test_unhashable_aircraft_identifier_rejected_as_validation_error(self):
        for identifier in ([], {}):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                validate_scenario(dict(type="aircraft_delay", aircraft_id=identifier, minutes=60), fixture())

    def test_active_aircraft_cannot_be_delayed(self):
        world = fixture()
        world["active"] = [{"aircraft_id": "A-0"}]
        with self.assertRaisesRegex(ValueError, "airborne"):
            validate_scenario(dict(type="aircraft_delay", aircraft_id="A-0", minutes=60), world)

    def test_scenario_effects(self):
        world = fixture()
        world["now"] = 10
        apply_scenario(world, dict(type="aircraft_delay", aircraft_id="A-0", minutes=60))
        self.assertEqual(world["aircraft"][0]["ready_at"], 70)
        apply_scenario(world, dict(type="base_closure", base="Harbor", minutes=30))
        self.assertEqual(world["bases"][0]["closed_until"], 40)
        apply_scenario(world, dict(type="relief_request", base="Harbor", priority=1, duration=45))
        self.assertEqual(len(world["tasks"]), 2)
        self.assertEqual(world["tasks"][-1]["earliest"], 25)


class PlannerTests(unittest.TestCase):
    def test_weights_validation_and_copy(self):
        self.assertEqual(validate_weights(DEFAULT_WEIGHTS), DEFAULT_WEIGHTS)
        self.assertIsNot(validate_weights(DEFAULT_WEIGHTS), DEFAULT_WEIGHTS)
        for bad in (True, float("nan"), float("inf"), -1, 101, "1", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_weights(dict(DEFAULT_WEIGHTS, risk=bad))
        for bad in ({}, [], dict(DEFAULT_WEIGHTS, extra=1), {k: 0 for k in DEFAULT_WEIGHTS}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_weights(bad)

    def test_oversized_weight_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            validate_weights(dict(DEFAULT_WEIGHTS, coverage=10**1000))

    def test_solver_repeatability_and_no_input_mutation(self):
        world = generate(13)
        before = deepcopy(world)
        self.assertEqual(stable(solve(world)), stable(solve(world)))
        self.assertEqual(world, before)
        for method in ("manual", "priority", "solver"):
            self.assertTrue(solve(world, method=method)["method"])
        with self.assertRaises(ValueError):
            solve(world, method="unknown")

    def test_windows_horizon_and_preparation(self):
        world = fixture()
        for request in (task(earliest=17), task(deadline=45), task(earliest=450, deadline=600)):
            assignment, _ = Reservations(world).candidate(request)
            self.assertIsNotNone(assignment)
            self.assertGreaterEqual(assignment["start"], max(15, request["earliest"]))
            self.assertLessEqual(assignment["end"], min(request["deadline"], world["horizon"]))
            self.assertEqual(assignment["start"] % 15, 0)
        for request in (task(deadline=44), task(earliest=480, deadline=600)):
            self.assertIsNone(Reservations(world).candidate(request)[0])
        world["aircraft"][0]["ready_at"] = 16
        self.assertEqual(Reservations(world).candidate(task())[0]["start"], 45)

    def test_qualifications_locality_rest_and_duty(self):
        for changes in ({"qualifications": ["helicopter"]}, {"base": "Ridge"},
                        {"ready_at": 46}, {"duty_used": 340}):
            world = fixture()
            world["crews"][0].update(changes)
            with self.subTest(changes=changes):
                self.assertIsNone(Reservations(world).candidate(task(deadline=75))[0])

    def test_missing_aircraft_and_expired_window_diagnoses(self):
        world = fixture()
        world["aircraft"][0]["kind"] = "helicopter"
        result = solve(world)
        self.assertIn("No aircraft", result["unscheduled"][0]["reasons"][0])
        world = fixture()
        world["now"] = 240
        result = solve(world)
        self.assertEqual(result["assignments"], [])
        self.assertIn("No departure slot", result["unscheduled"][0]["reasons"][0])
        self.assertIn("No tested single-constraint", result["unscheduled"][0]["counterfactuals"][0])

    def test_rest_overlap_turnaround_and_duty_across_waves(self):
        world = fixture()
        reservations = Reservations(world)
        first, _ = reservations.candidate(task())
        reservations.reserve(first, task())
        second, _ = reservations.candidate(task("T-2"))
        self.assertGreaterEqual(second["start"], first["end"] + 60)
        self.assertGreaterEqual(second["start"] - 15, first["end"] + 30)
        self.assertEqual(reservations.crews["C-0"]["duty_used"], 30)
        world["crews"][0]["rest_minutes"] = 0
        reservations = Reservations(world)
        reservations.reserve(first, task())
        second, _ = reservations.candidate(task("T-2"))
        self.assertEqual(second["start"], first["end"] + 45)

    def test_each_capacity_blocks_and_active_flights_reserve_arrivals(self):
        for resource in ("runway", "loading", "ground"):
            world = fixture(2)
            world["bases"][0][resource + "_slots"] = 1
            reservations = Reservations(world)
            first, _ = reservations.candidate(task(deadline=45))
            reservations.reserve(first, task())
            with self.subTest(resource=resource):
                second, reasons = reservations.candidate(task("T-2", deadline=45))
                self.assertIsNone(second)
                self.assertTrue(any("capacity" in r for r in reasons))
        world = fixture(2)
        world["bases"][0]["runway_slots"] = 1
        world["active"] = [dict(base="Harbor", end=45)]
        self.assertIsNone(Reservations(world).candidate(task(deadline=45))[0])

    def test_closure_includes_preparation(self):
        world = fixture()
        world["bases"][0]["closed_until"] = 16
        self.assertEqual(Reservations(world).candidate(task())[0]["start"], 45)

    def test_inventory_and_tank_reserve(self):
        for resource, amount in (("supplies", 5), ("fuel_stock", 24)):
            world = fixture(2)
            if resource == "fuel_stock":
                for aircraft in world["aircraft"]:
                    aircraft["fuel"] = 20
            world["bases"][0][resource] = amount
            reservations = Reservations(world)
            first, _ = reservations.candidate(task())
            reservations.reserve(first, task())
            with self.subTest(resource=resource):
                self.assertEqual(reservations.bases["Harbor"][resource], 0)
                self.assertIsNone(reservations.candidate(task("T-2"))[0])
        world = fixture()
        world["aircraft"][0].update(fuel=10, capacity=44)
        reservations = Reservations(world)
        assignment, _ = reservations.candidate(task())
        self.assertEqual(assignment["refuel"], 34)
        reservations.reserve(assignment, task())
        self.assertEqual(reservations.aircraft["A-0"]["fuel"], 20)
        self.assertEqual(reservations.bases["Harbor"]["fuel_stock"], world["bases"][0]["fuel_stock"] - 34)
        world["aircraft"][0]["capacity"] = 43
        self.assertIsNone(Reservations(world).candidate(task())[0])

    def test_onboard_fuel_does_not_require_base_refueling(self):
        world = fixture()
        world["bases"][0]["fuel_stock"] = 0
        assignment, _ = Reservations(world).candidate(task())
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment["refuel"], 0)

    def test_generated_plan_constraints_independently(self):
        for seed in (1, 42, 97):
            world = generate(seed)
            result = solve(world)
            tasks = {t["id"]: t for t in world["tasks"]}
            aircraft = {a["id"]: deepcopy(a) for a in world["aircraft"]}
            crews = {c["id"]: deepcopy(c) for c in world["crews"]}
            bases = {b["id"]: deepcopy(b) for b in world["bases"]}
            slots = Counter()
            seen = set()
            for assignment in sorted(result["assignments"], key=lambda a: a["start"]):
                t = tasks[assignment["task_id"]]
                a, c, b = aircraft[assignment["aircraft_id"]], crews[assignment["crew_id"]], bases[assignment["base"]]
                start, end = assignment["start"], assignment["end"]
                self.assertNotIn(t["id"], seen)
                seen.add(t["id"])
                self.assertEqual((a["base"], c["base"], b["id"]), (t["base"],) * 3)
                self.assertEqual(a["kind"], t["aircraft_kind"])
                self.assertIn(a["kind"], c["qualifications"])
                self.assertGreaterEqual(start, t["earliest"])
                self.assertLessEqual(end, min(t["deadline"], world["horizon"]))
                self.assertEqual(end - start, t["duration"])
                self.assertGreaterEqual(start - 15, max(a["ready_at"], b["closed_until"]))
                self.assertGreaterEqual(start, c["ready_at"])
                a["ready_at"], c["ready_at"] = end + 30, end + c["rest_minutes"]
                c["duty_used"] += t["duration"]
                self.assertLessEqual(c["duty_used"], c["duty_limit"])
                a["fuel"] += assignment["refuel"]
                self.assertLessEqual(a["fuel"], a["capacity"])
                a["fuel"] -= t["fuel"]
                self.assertGreaterEqual(a["fuel"], 20)
                for resource, amount in (("supplies", t["supplies"]), ("fuel_stock", assignment["refuel"])):
                    b[resource] -= amount
                    self.assertGreaterEqual(b[resource], 0)
                uses = [("runway", start // 15), ("runway", end // 15), ("loading", start // 15 - 1)]
                uses += [("ground", slot) for slot in range(end // 15, (end + 30) // 15)]
                for resource, slot in uses:
                    slots[b["id"], resource, slot] += 1
                    self.assertLessEqual(slots[b["id"], resource, slot], b[resource + "_slots"])

    def test_impossible_diagnosis_and_tested_single_relaxation(self):
        world = fixture()
        world["bases"][0]["supplies"] = 0
        result = construct(world, world["tasks"], [], diagnose=True)
        failure = result["unscheduled"][0]
        self.assertEqual(result["assignments"], [])
        self.assertTrue(any("supplies" in reason for reason in failure["reasons"]))
        self.assertEqual(len(failure["counterfactuals"]), 1)
        self.assertIn("Additional relief supplies alone permits departure", failure["counterfactuals"][0])
        self.assertIsNotNone(Reservations(world).candidate(task(), ignore="supplies")[0])
        world["bases"][0]["fuel_stock"] = 0
        world["aircraft"][0]["fuel"] = 20
        failure = construct(world, world["tasks"], [], diagnose=True)["unscheduled"][0]
        self.assertIn("No tested single-constraint relaxation", failure["counterfactuals"][0])

    def test_counterfactual_holds_earlier_inventory_allocations_fixed(self):
        world = fixture(2)
        world["bases"][0]["supplies"] = 5
        world["tasks"].append(task("T-2"))
        result = construct(world, world["tasks"], [], diagnose=True)
        self.assertEqual([a["task_id"] for a in result["assignments"]], ["T-1"])
        self.assertEqual(result["unscheduled"][0]["task_id"], "T-2")
        self.assertIn("holding earlier allocations fixed", result["unscheduled"][0]["counterfactuals"][0])

    def test_robustness_repeatability_intervals_missing_p1_and_no_p1(self):
        world = generate(42)
        plan = solve(world)
        self.assertEqual(robustness(world, plan), robustness(world, plan))
        for count in (1, 16, 128):
            result = robustness(world, plan, count)
            low, high = result["robustness_ci"]
            self.assertLessEqual(0, low)
            self.assertLessEqual(low, result["robustness"])
            self.assertGreaterEqual(high, result["robustness"])
            self.assertLessEqual(high, 1)
            self.assertEqual(result["samples"], count)
        world = fixture()
        result = robustness(world, {"assignments": []})
        self.assertEqual(result["robustness"], 0)
        self.assertEqual(result["p1_count"], 1)
        self.assertGreater(result["robustness_ci"][1], 0)
        selected = solve(world)
        world["tasks"].append(task("MISSING-P1"))
        self.assertEqual(robustness(world, selected)["robustness"], 0)
        world["tasks"].pop()
        world["tasks"][0]["priority"] = 2
        result = robustness(world, {"assignments": []})
        self.assertEqual(result["robustness"], 1)
        self.assertEqual(result["p1_count"], 0)
        self.assertIn("No P1 tasks", result["robustness_note"])
        self.assertIsNone(robustness(world, plan, 0)["robustness"])

    def test_frontier_is_unique_and_nondominated_among_all_sampled_plans(self):
        world = generate(9)
        result = frontier(world)
        self.assertTrue(result["plans"])
        self.assertIn("not a proven global", result["note"])
        def vector(metrics):
            return (-metrics["coverage"], metrics["risk"], metrics["disruption"], metrics["fuel"])
        all_vectors = [vector(solve(world, weights={k: 15 if k == focus else 1 for k in DEFAULT_WEIGHTS},
                                    samples=64, diagnose=False)["metrics"]) for focus in DEFAULT_WEIGHTS]
        vectors = [vector(p["metrics"]) for p in result["plans"]]
        self.assertEqual(len(vectors), len(set(vectors)))
        for candidate in vectors:
            self.assertFalse(any(all(x <= y for x, y in zip(other, candidate)) and
                                 any(x < y for x, y in zip(other, candidate)) for other in all_vectors))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "audit.jsonl"
        self.audit = AuditLog(self.path)

    def test_append_reopen_and_chain(self):
        self.assertEqual(self.audit.verify()["entries"], 0)
        for index in range(4):
            head = self.audit.append("test", {"index": index})
        status = AuditLog(self.path).verify()
        self.assertTrue(status["valid"])
        self.assertEqual(status["entries"], 4)
        self.assertEqual(status["head"], head)

    def test_tampering_reordering_deleting_middle_and_malformed_lines(self):
        for index in range(4):
            self.audit.append("test", {"index": index})
        original = self.path.read_text(encoding="utf-8").splitlines()
        changed = json.loads(original[1])
        changed["payload"]["index"] = 99
        variants = [original[:1] + [json.dumps(changed)] + original[2:],
                    [original[0], original[2], original[1], original[3]],
                    original[:1] + original[2:], original[:1] + ["not json"],
                    original[:1] + ["[]"]]
        for lines in variants:
            with self.subTest(lines=lines):
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.assertFalse(self.audit.verify()["valid"])
                with self.assertRaises(ValueError):
                    AuditLog(self.path)
                with self.assertRaises(ValueError):
                    self.audit.append("no", {})


class EngineTests(unittest.TestCase):
    REVIEWERS = {"test-alice": "alice-test-secret", "test-bob": "bob-test-secret"}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = self.make_engine("first")

    def make_engine(self, name):
        return Engine(AuditLog(Path(self.directory.name) / (name + ".jsonl")), dict(self.REVIEWERS), seed=23)

    def scenario(self):
        return dict(type="base_closure", base="Harbor", minutes=30)

    def approve(self, proposal, reviewer="test-alice"):
        self.engine.approve(proposal, reviewer, self.REVIEWERS[reviewer])

    def test_reviewer_credentials_must_be_distinct(self):
        for reviewers in ({"a": "same", "b": "same"}, {"a": "only"}):
            with self.assertRaises(ValueError):
                Engine(self.engine.audit, reviewers)

    def test_preview_duplicate_review_and_two_distinct_apply_once(self):
        before = deepcopy(self.engine.world)
        proposal = self.engine.preview(self.scenario())["proposal_id"]
        self.assertEqual(self.engine.world, before)
        with self.assertRaises(PermissionError):
            self.engine.approve(proposal, "test-alice", "wrong")
        self.approve(proposal)
        self.assertEqual(self.engine.world, before)
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.approve(proposal)
        self.assertEqual(self.engine.pending[proposal]["approvals"], ["test-alice"])
        self.approve(proposal, "test-bob")
        self.assertEqual(self.engine.pending[proposal]["status"], "applied")
        self.assertEqual(self.engine.world["bases"][0]["closed_until"], 30)
        with self.assertRaisesRegex(ValueError, "no longer pending"):
            self.approve(proposal)
        records = [json.loads(line) for line in self.engine.audit.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(r["kind"] == "reviewed_scenario_applied" for r in records), 1)

    def test_stale_approval_after_clock_and_preferences_change(self):
        for change in ("clock", "preferences"):
            with self.subTest(change=change):
                self.engine.reset(23)
                proposal = self.engine.preview(self.scenario())["proposal_id"]
                self.approve(proposal)
                if change == "clock":
                    self.engine.control({"paused": False, "speed": 60})
                    self.engine.advance(1)
                else:
                    self.engine.preferences(dict(DEFAULT_WEIGHTS, coverage=6))
                with self.assertRaisesRegex(ValueError, "State changed"):
                    self.approve(proposal, "test-bob")
                self.assertEqual(self.engine.pending[proposal]["status"], "stale")
                self.assertEqual(self.engine.world["bases"][0]["closed_until"], 0)

    def test_cache_hit_bypasses_solver_and_returns_isolated_preview(self):
        cached = next(iter(self.engine.cache.values()))
        original_cached = deepcopy(cached)
        before = deepcopy(self.engine.world)
        with patch("civilian_sim.engine.solve", side_effect=AssertionError("cache miss")):
            result = self.engine.preview(cached["scenario"])
        self.assertTrue(result["cache_hit"])
        self.assertEqual(result["preview"], cached["plan"]["metrics"])
        self.assertEqual(self.engine.world, before)
        self.engine.pending[result["proposal_id"]]["plan"]["assignments"].clear()
        self.assertEqual(self.engine.cache[cached["scenario"]["aircraft_id"]], original_cached)
        self.engine.control({"paused": False, "speed": 60})
        self.engine.advance(1)
        self.assertFalse(self.engine.preview(cached["scenario"])["cache_hit"])

    def test_pause_speed_and_invalid_controls_are_atomic(self):
        self.engine.advance(3600)
        self.assertEqual(self.engine.world["now"], 0)
        for invalid in ({"paused": 1}, {"speed": True}, {"speed": float("nan")},
                        {"speed": float("inf")}, {"speed": 0}, {"speed": 61}, {"extra": 1}):
            before = (self.engine.paused, self.engine.speed, self.engine.audit.verify()["entries"])
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.engine.control(invalid)
            self.assertEqual((self.engine.paused, self.engine.speed, self.engine.audit.verify()["entries"]), before)
        self.engine.control({"paused": False, "speed": 30})
        self.engine.advance(2)
        self.assertEqual(self.engine.world["now"], 1)
        self.engine.advance(-10)
        self.assertEqual(self.engine.world["now"], 1)
        self.engine.control({"paused": True})
        self.engine.advance(10)
        self.assertEqual(self.engine.world["now"], 1)

    def test_oversized_speed_is_a_validation_error(self):
        with self.assertRaises(ValueError):
            self.engine.control({"speed": 10**1000})

    def test_horizon_pauses_and_never_advances_beyond_end(self):
        self.engine.world = fixture()
        self.engine.world["horizon"] = 60
        self.engine.plan = solve(self.engine.world)
        self.engine.next_event = 1000
        self.engine.control({"paused": False, "speed": 60})
        self.engine.advance(100)
        self.assertEqual(self.engine.world["now"], 60)
        self.assertTrue(self.engine.paused)
        self.engine.advance(100)
        self.assertEqual(self.engine.world["now"], 60)

    def test_clock_partition_determinism_and_all_event_types(self):
        other = self.make_engine("second")
        for engine in (self.engine, other):
            engine.control({"paused": False, "speed": 60})
        self.engine.advance(120)
        for _ in range(120):
            other.advance(1)
        self.assertEqual(self.engine.world, other.world)
        self.assertEqual(self.engine.events, other.events)
        self.assertEqual(stable(self.engine.plan), stable(other.plan))
        self.assertEqual(self.engine.version, other.version)
        self.assertEqual({e["kind"] for e in self.engine.events}, {"aircraft_delay", "base_closure", "relief_request"})
        def records(engine):
            return [(r["kind"], r["payload"]) for r in map(json.loads, engine.audit.path.read_text(encoding="utf-8").splitlines())]
        self.assertEqual(records(self.engine), records(other))

    def test_dispatch_arrival_and_committed_resources(self):
        self.engine.world = fixture()
        self.engine.world["aircraft"][0]["fuel"] = 20
        self.engine.plan = solve(self.engine.world)
        self.engine.next_event = 1000
        self.engine.control({"paused": False, "speed": 60})
        supplies = self.engine.world["bases"][0]["supplies"]
        fuel_stock = self.engine.world["bases"][0]["fuel_stock"]
        self.engine.advance(15)
        self.assertEqual(self.engine.world["tasks"][0]["status"], "in_flight")
        self.assertEqual(len(self.engine.world["active"]), 1)
        self.assertEqual(self.engine.world["aircraft"][0]["ready_at"], 75)
        self.assertEqual(self.engine.world["crews"][0]["ready_at"], 105)
        self.assertEqual(self.engine.world["bases"][0]["supplies"], supplies - 5)
        self.assertEqual(self.engine.world["bases"][0]["fuel_stock"], fuel_stock - 24)
        self.assertEqual(self.engine.world["aircraft"][0]["fuel"], 20)
        self.engine.advance(30)
        self.assertEqual(self.engine.world["tasks"][0]["status"], "completed")
        self.assertEqual(self.engine.world["active"], [])
        self.assertEqual(self.engine.world["bases"][0]["supplies"], supplies - 5)
        self.assertEqual(self.engine.world["bases"][0]["fuel_stock"], fuel_stock - 24)

    def test_reset_replays_and_state_is_a_copy(self):
        before = stable(self.engine.state())
        state = self.engine.state()
        state["aircraft"].clear()
        self.assertTrue(self.engine.world["aircraft"])
        self.engine.control({"paused": False, "speed": 60})
        self.engine.advance(35)
        self.engine.reset(23)
        after = stable(self.engine.state())
        for key in ("plan", "tasks", "aircraft", "bases", "events", "pending", "sim_minute", "paused"):
            self.assertEqual(before[key], after[key])


if __name__ == "__main__":
    unittest.main()