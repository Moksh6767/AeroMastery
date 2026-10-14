"""Explicit synthetic assumptions; never reads the repository's military data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import random

EPOCH = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
BASE_NAMES = ("Harbor", "Meadow", "Ridge")


def timestamp(minute):
    return (EPOCH + timedelta(minutes=minute)).isoformat()


def generate(seed=42, size=20):
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer between 0 and 2147483647")
    if type(size) is not int or not 3 <= size <= 300:
        raise ValueError("fleet size must be an integer between 3 and 300")
    rng = random.Random(seed)
    bases = [{"id": name, "runway_slots": max(1, size // 30),
              "loading_slots": max(2, size // 15),
              "ground_slots": max(2, size // 15),
              "supplies": size * 18, "fuel_stock": size * 250,
              "closed_until": 0, "weather_failure": rng.uniform(.03, .12)}
             for name in BASE_NAMES]
    aircraft, crews = [], []
    for i in range(size):
        base = BASE_NAMES[i % 3]
        kind = "helicopter" if i % 2 else "transport"
        aircraft.append({"id": f"CV-{i+1:03d}", "base": base, "kind": kind,
                         "ready_at": rng.choice([0, 0, 0, 30, 90]),
                         "fuel": 180, "capacity": 180,
                         "serviceability": round(rng.uniform(.88, .99), 4)})
        crews.append({"id": f"RC-{i+1:03d}", "base": base,
                      "qualifications": [kind], "ready_at": rng.choice([0, 0, 30, 60]),
                      "rest_minutes": 60, "duty_used": 0, "duty_limit": 360})
    tasks = []
    for i in range(round(size * 1.4)):
        earliest = rng.randrange(15, 240, 15)
        duration = rng.choice([30, 45, 60, 90])
        tasks.append({"id": f"REL-{i+1:03d}",
                      "kind": rng.choice(["medical evacuation", "relief delivery", "rescue transport"]),
                      "priority": rng.choices([1, 2, 3], [3, 4, 3])[0],
                      "base": rng.choice(BASE_NAMES),
                      "aircraft_kind": rng.choice(["helicopter", "transport"]),
                      "earliest": earliest, "deadline": earliest + duration + rng.choice([60, 90, 120]),
                      "duration": duration, "supplies": rng.randint(2, 12),
                      "fuel": duration * .8, "status": "pending"})
    return {"seed": seed, "now": 0., "horizon": 480, "bases": bases,
            "aircraft": aircraft, "crews": crews, "tasks": tasks, "active": []}


def validate_scenario(value, world):
    if not isinstance(value, dict):
        raise ValueError("scenario must be an object")
    schemas = {"aircraft_delay": {"type", "aircraft_id", "minutes"},
               "base_closure": {"type", "base", "minutes"},
               "relief_request": {"type", "base", "priority", "duration"}}
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in schemas or set(value) != schemas[kind]:
        raise ValueError("Unsupported scenario or missing/extra fields")
    if kind == "aircraft_delay":
        if not isinstance(value["aircraft_id"], str) or value["aircraft_id"] not in {a["id"] for a in world["aircraft"]}:
            raise ValueError("Unknown civilian aircraft")
        if value["aircraft_id"] in {a["aircraft_id"] for a in world["active"]}:
            raise ValueError("Cannot delay an airborne aircraft")
    elif not isinstance(value["base"], str) or value["base"] not in BASE_NAMES:
        raise ValueError("Unknown fictional civilian base")
    for key, low, high in (("minutes", 15, 240), ("priority", 1, 3), ("duration", 30, 120)):
        if key in value and (type(value[key]) is not int or not low <= value[key] <= high):
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    return deepcopy(value)


def apply_scenario(world, scenario):
    value = validate_scenario(scenario, world)
    if value["type"] == "aircraft_delay":
        aircraft = next(a for a in world["aircraft"] if a["id"] == value["aircraft_id"])
        aircraft["ready_at"] = max(aircraft["ready_at"], world["now"] + value["minutes"])
    elif value["type"] == "base_closure":
        base = next(b for b in world["bases"] if b["id"] == value["base"])
        base["closed_until"] = max(base["closed_until"], world["now"] + value["minutes"])
    else:
        index = len(world["tasks"]) + 1
        world["tasks"].append({"id": f"REL-{index:03d}", "kind": "urgent relief delivery",
                               "priority": value["priority"], "base": value["base"],
                               "aircraft_kind": "helicopter", "earliest": world["now"] + 15,
                               "deadline": world["now"] + value["duration"] + 120,
                               "duration": value["duration"], "supplies": 5,
                               "fuel": value["duration"] * .8, "status": "pending"})