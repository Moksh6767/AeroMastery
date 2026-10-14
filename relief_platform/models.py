from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field as PField
from sqlalchemy import Column, JSON, event
from sqlmodel import SQLModel, Field


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Reason(StrEnum):
    PRIORITY = "PRIORITY_COVERAGE"
    READINESS = "AIRCRAFT_NOT_READY"
    QUALIFICATION = "CREW_QUALIFICATION"
    DUTY = "CREW_DUTY_OR_REST"
    WEATHER = "WEATHER_CLOSURE"
    AIRSPACE = "CIVILIAN_AIRSPACE_CLOSURE"
    WINDOW = "TIME_WINDOW"
    FUEL = "FUEL_OR_SUPPLY_CAPACITY"
    CAPACITY = "RESOURCE_CONTENTION"
    RISK = "SYNTHETIC_RISK_PENALTY"
    STABILITY = "BRIEFED_PLAN_STABILITY"
    CONFLICT = "SOURCE_CONFLICT"
    SCENARIO = "SYNTHETIC_SCENARIO_LOAD"
    GENERATION = "PLAN_CANDIDATE_GENERATED"
    DISRUPTION = "CIVILIAN_DISRUPTION_PREVIEW"
    APPROVAL = "HUMAN_APPROVAL"
    REJECTION = "HUMAN_REJECTION"
    MODIFICATION = "PROPOSAL_MODIFIED_REVIEW_REQUIRED"


class Provenance(SQLModel):
    source: str = "synthetic-generator"
    timestamp: str = Field(default_factory=utcnow)
    confidence: float = Field(default=1.0, ge=0, le=1)
    synthetic: bool = True


class Entity(Provenance):
    id: str = Field(primary_key=True)
    scenario_id: str = Field(index=True)
    payload: dict = Field(default_factory=dict, sa_type=JSON)


class Base(Entity, table=True):
    pass


class Aircraft(Entity, table=True):
    pass


class Crew(Entity, table=True):
    pass


class CargoLoad(Entity, table=True):
    pass


class ReliefMission(Entity, table=True):
    pass


class SortieSlot(Entity, table=True):
    pass


class SupportAsset(Entity, table=True):
    pass


class AirspaceZone(Entity, table=True):
    pass


class WeatherCell(Entity, table=True):
    pass


class MaintenanceRecord(Entity, table=True):
    pass


class Assignment(Entity, table=True):
    pass


class Plan(Entity, table=True):
    pass


class PlanDiff(Entity, table=True):
    pass


class Scenario(Provenance, table=True):
    id: str = Field(primary_key=True)
    preset: str
    seed: int
    revision: int = 1
    world: dict = Field(sa_column=Column(JSON, nullable=False))
    feeds: list = Field(sa_column=Column(JSON, nullable=False))
    fusion: dict = Field(sa_column=Column(JSON, nullable=False))


class PlanVersion(Provenance, table=True):
    id: str = Field(primary_key=True)
    scenario_id: str = Field(index=True)
    number: int
    revision: int
    approved: bool = False
    plan: dict = Field(sa_column=Column(JSON, nullable=False))


class RetaskingProposal(Provenance, table=True):
    id: str = Field(primary_key=True)
    scenario_id: str = Field(index=True)
    revision: int
    base_plan_id: str
    status: str = "pending"
    event: dict = Field(sa_column=Column(JSON, nullable=False))
    options: list = Field(sa_column=Column(JSON, nullable=False))


class AuditEntry(Provenance, table=True):
    id: int | None = Field(default=None, primary_key=True)
    actor: str
    action: str
    reason_codes: list = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    before: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    after: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))


@event.listens_for(AuditEntry, "before_update")
@event.listens_for(AuditEntry, "before_delete")
def immutable_audit(*args):
    raise ValueError("Audit entries are append-only")


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ScenarioRequest(Request):
    preset: Literal[
        "baseline", "high-tempo", "degraded-weather", "many-aircraft-down"
    ] = "baseline"
    seed: int = PField(default=42, ge=0, le=2**31 - 1)
    size: int = PField(default=9, ge=3, le=24)


class Disruption(Request):
    type: Literal[
        "aircraft_unserviceable",
        "weather_closure",
        "airspace_closure",
        "priority_change",
    ]
    target: str
    minutes: int = PField(default=90, ge=15, le=480)
    priority: int = PField(default=1, ge=1, le=3)


class Review(Request):
    option: int = PField(default=0, ge=0, le=2)
    reason: str = PField(min_length=3, max_length=500)


class Chat(Request):
    text: str = PField(min_length=1, max_length=2000)
