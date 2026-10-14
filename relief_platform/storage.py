from copy import deepcopy
from .models import (
    Base,
    Aircraft,
    Crew,
    ReliefMission,
    MaintenanceRecord,
    WeatherCell,
    AirspaceZone,
    CargoLoad,
    SupportAsset,
    SortieSlot,
    Assignment,
    Plan,
    PlanDiff,
)


def upsert(
    session,
    cls,
    scenario_id,
    identity,
    payload,
    source="synthetic-generator",
    provenance=None,
):
    key = scenario_id + ":" + identity
    row = session.get(cls, key)
    if row:
        row.payload = deepcopy(payload)
    else:
        row = cls(
            id=key,
            scenario_id=scenario_id,
            payload=deepcopy(payload),
            **(provenance or {"source": source})
        )
    session.add(row)


def save_entities(session, row):
    for name, cls in (
        ("bases", Base),
        ("aircraft", Aircraft),
        ("crews", Crew),
        ("tasks", ReliefMission),
        ("maintenance", MaintenanceRecord),
    ):
        for value in row.world[name]:
            upsert(
                session,
                cls,
                row.id,
                value["id"],
                value,
                provenance=value.get("provenance"),
            )
    for b in row.world["bases"]:
        upsert(
            session,
            WeatherCell,
            row.id,
            "weather:" + b["id"],
            {
                "base_id": b["id"],
                "closed_until": b["closed_until"],
                "risk": b["weather_failure"],
            },
            "weather",
        )
        upsert(
            session,
            SupportAsset,
            row.id,
            "ground:" + b["id"],
            {
                "base_id": b["id"],
                "loading_slots": b["loading_slots"],
                "ground_slots": b["ground_slots"],
                "fuel_stock": b["fuel_stock"],
            },
        )
        upsert(
            session,
            CargoLoad,
            row.id,
            "cargo:" + b["id"],
            {
                "base_id": b["id"],
                "relief_supply_units": b["supplies"],
                "compatible_aircraft_types": ["transport", "helicopter"],
            },
        )
    for z in row.world["zones"]:
        upsert(session, AirspaceZone, row.id, z["id"], z, "airspace")


def save_plan(session, version, delta=None):
    upsert(
        session,
        Plan,
        version.scenario_id,
        version.id,
        {
            "version_id": version.id,
            "number": version.number,
            "approved": version.approved,
        },
    )
    for a in version.plan["assignments"]:
        identity = version.id + ":" + a["task_id"]
        upsert(session, Assignment, version.scenario_id, identity, a, "cp-sat")
        upsert(
            session,
            SortieSlot,
            version.scenario_id,
            identity,
            {
                "start": a["start"],
                "end": a["end"],
                "aircraft_id": a["aircraft_id"],
                "crew_id": a["crew_id"],
            },
            "cp-sat",
        )
    if delta is not None:
        upsert(
            session, PlanDiff, version.scenario_id, version.id, delta, "human-review"
        )
