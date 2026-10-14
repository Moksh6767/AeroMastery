"""Seeded, fictional civilian scenarios and independently timestamped source feeds."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from civilian_sim.model import generate
from .models import Disruption

EPOCH = datetime(2026, 10, 14, 8, tzinfo=timezone.utc)
TRUST = {
    "maintenance": 100,
    "crew-roster": 95,
    "weather": 90,
    "airspace": 95,
    "relief-priorities": 90,
    "dispatch-cache": 40,
}
FIELDS = {
    "maintenance": {"aircraft": {"ready_at", "serviceability"}},
    "crew-roster": {"crews": {"ready_at", "duty_used", "duty_limit"}},
    "weather": {"bases": {"closed_until", "weather_failure"}},
    "airspace": {"tasks": {"airspace_closed_until"}},
    "relief-priorities": {"tasks": {"priority"}},
    "dispatch-cache": {"aircraft": {"ready_at"}},
}


def scenario(seed, size, preset):
    world = generate(seed, size)
    world["label"] = "SYNTHETIC DATA - CIVILIAN ADVISORY ONLY"
    world["preset"] = preset
    positions = [(73.0, 18.0), (74.0, 19.0), (75.0, 18.5)]
    for base, (lon, lat) in zip(world["bases"], positions):
        base.update(longitude=lon, latitude=lat)
    for i, task in enumerate(world["tasks"]):
        base = next(b for b in world["bases"] if b["id"] == task["base"])
        task.update(
            destination=[
                base["longitude"] + 0.1 + (i % 4) * 0.05,
                base["latitude"] + 0.15,
            ],
            airspace_closed_until=0,
        )
    world["zones"] = []
    world["maintenance"] = [
        {
            "id": f"MX-{i}",
            "aircraft_id": a["id"],
            "hours": 50 + i * 10,
            "faults": i % 4,
            "days_since_service": 5 + i % 20,
        }
        for i, a in enumerate(world["aircraft"])
    ]
    if preset == "high-tempo":
        extra = deepcopy(world["tasks"])
        for i, task in enumerate(extra):
            task["id"] = f"REL-{len(world['tasks']) + i + 1:03d}"
            task["priority"] = 1
        world["tasks"].extend(extra)
    elif preset == "degraded-weather":
        for base in world["bases"]:
            base.update(closed_until=120, weather_failure=0.45)
    elif preset == "many-aircraft-down":
        for a in world["aircraft"][::2]:
            a["ready_at"] = 480
    provenance = {
        "source": "synthetic-generator",
        "timestamp": EPOCH.isoformat(),
        "confidence": 1.0,
        "synthetic": True,
    }
    for kind in ("bases", "aircraft", "crews", "tasks", "maintenance"):
        for record in world[kind]:
            record["provenance"] = dict(provenance)
    feeds = []
    for source, mappings in FIELDS.items():
        for entity, fields in mappings.items():
            for row in world[entity]:
                values = {f: row[f] for f in fields}
                if source == "dispatch-cache":
                    values["ready_at"] = 0
                feeds.append(
                    {
                        "source": source,
                        "entity": entity,
                        "id": row["id"],
                        "values": values,
                        "timestamp": (
                            EPOCH
                            - timedelta(minutes=90 if source == "dispatch-cache" else 0)
                        ).isoformat(),
                        "confidence": 0.65 if source == "dispatch-cache" else 0.95,
                        "synthetic": True,
                    }
                )
    return world, feeds


def fuse(world, feeds, now=EPOCH):
    result = deepcopy(world)
    grouped, conflicts, sources = {}, [], {}
    for feed in feeds:
        source = feed["source"]
        if source not in FIELDS or feed["entity"] not in FIELDS[source]:
            raise ValueError("Unknown source adapter/entity")
        age = max(
            0.0, (now - datetime.fromisoformat(feed["timestamp"])).total_seconds() / 60
        )
        import math

        sources[source] = {
            "age_minutes": age,
            "freshness": math.exp(-age / 60),
            "stale": age > 60,
        }
        for field, value in feed["values"].items():
            if field not in FIELDS[source][feed["entity"]]:
                raise ValueError("Source cannot update that field")
            grouped.setdefault((feed["entity"], feed["id"], field), []).append(
                (feed, value)
            )
    for (entity, record_id, field), candidates in grouped.items():
        candidates.sort(
            key=lambda x: (
                TRUST[x[0]["source"]],
                datetime.fromisoformat(x[0]["timestamp"]),
            ),
            reverse=True,
        )
        winner, value = candidates[0]
        record = next((r for r in result[entity] if r["id"] == record_id), None)
        if record is None:
            raise ValueError("Unknown feed record")
        tie = any(
            TRUST[c[0]["source"]] == TRUST[winner["source"]]
            and c[0]["timestamp"] == winner["timestamp"]
            and c[1] != value
            for c in candidates[1:]
        )
        if tie:
            # Keep prior field; ambiguous readiness must not authorize scheduling.
            if entity == "aircraft":
                record["fusion_blocked"] = True
            if entity == "crews":
                record["fusion_blocked"] = True
            if entity == "bases":
                record["fusion_blocked"] = True
            if entity == "tasks":
                record["fusion_blocked"] = True
        else:
            record[field] = value
        record.setdefault("field_provenance", {})[field] = {
            k: winner[k] for k in ("source", "timestamp", "confidence", "synthetic")
        }
        if any(c[1] != value for c in candidates[1:]):
            conflicts.append(
                {
                    "record_id": record_id,
                    "field": field,
                    "values": [
                        {"source": c[0]["source"], "value": c[1]} for c in candidates
                    ],
                    "resolution": (
                        "human-review-required" if tie else "trust-ranking-then-latest"
                    ),
                    "chosen_source": None if tie else winner["source"],
                }
            )
    return result, {
        "sources": sources,
        "conflicts": conflicts,
        "timestamp": now.isoformat(),
    }


def disrupt(world, event: Disruption):
    world = deepcopy(world)
    field = {
        "aircraft_unserviceable": "aircraft",
        "weather_closure": "bases",
        "priority_change": "tasks",
        "airspace_closure": "tasks",
    }[event.type]
    row = next((r for r in world[field] if r["id"] == event.target), None)
    if row is None:
        raise ValueError("Unknown civilian disruption target")
    if event.type == "aircraft_unserviceable":
        row["ready_at"] = max(row["ready_at"], event.minutes)
    elif event.type == "weather_closure":
        row["closed_until"] = max(row["closed_until"], event.minutes)
    elif event.type == "priority_change":
        row["priority"] = event.priority
    else:
        row["airspace_closed_until"] = max(row["airspace_closed_until"], event.minutes)
        lon, lat = row["destination"]
        world["zones"].append(
            {
                "id": f"CLOSED-{len(world['zones'])+1}",
                "until": event.minutes,
                "polygon": [
                    [lon - 0.02, lat - 0.02],
                    [lon + 0.02, lat - 0.02],
                    [lon + 0.02, lat + 0.02],
                    [lon - 0.02, lat + 0.02],
                ],
                "provenance": {
                    "source": "synthetic-airspace",
                    "timestamp": EPOCH.isoformat(),
                    "confidence": 1.0,
                    "synthetic": True,
                },
            }
        )
    changed_field = {
        "aircraft_unserviceable": "ready_at",
        "weather_closure": "closed_until",
        "priority_change": "priority",
        "airspace_closure": "airspace_closed_until",
    }[event.type]
    provenance = {
        "source": "synthetic-disruption",
        "timestamp": EPOCH.isoformat(),
        "confidence": 1.0,
        "synthetic": True,
    }
    row["provenance"] = provenance
    row.setdefault("field_provenance", {})[changed_field] = dict(provenance)
    return world
