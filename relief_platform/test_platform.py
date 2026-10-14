from copy import deepcopy
import json
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session
from .api import create_app
from .models import Disruption, AuditEntry
from .scenarios import scenario, fuse, disrupt, EPOCH
from .optimizer import solve, diff
from .copilot import answer

TOKENS = {
    "planner": "test-planner",
    "approver": "test-approver",
    "viewer": "test-viewer",
}
HEADERS = {r: {"Authorization": "Bearer " + t} for r, t in TOKENS.items()}


def tiny_world():
    w, _ = scenario(42, 3, "baseline")
    w["tasks"] = w["tasks"][:1]
    t = w["tasks"][0]
    t.update(
        base="Harbor", aircraft_kind="transport", earliest=15, deadline=150, duration=30
    )
    w["aircraft"][0]["ready_at"] = 0
    w["crews"][0]["ready_at"] = 0
    return w


def test_seed_and_presets():
    assert scenario(12, 6, "baseline") == scenario(12, 6, "baseline")
    w, _ = scenario(12, 6, "many-aircraft-down")
    assert sum(a["ready_at"] == 480 for a in w["aircraft"]) == 3
    assert all(r["provenance"]["synthetic"] for r in w["tasks"])


def test_fusion_trust_staleness_and_tie():
    w, feeds = scenario(42, 3, "baseline")
    w["aircraft"][0]["ready_at"] = 120
    primary = next(
        f for f in feeds if f["source"] == "maintenance" and f["id"] == "CV-001"
    )
    primary["values"]["ready_at"] = 120
    fused, report = fuse(w, feeds)
    assert fused["aircraft"][0]["ready_at"] == 120
    assert report["sources"]["dispatch-cache"]["stale"]
    assert report["conflicts"][0]["resolution"] == "trust-ranking-then-latest"
    duplicate = deepcopy(primary)
    duplicate["values"]["ready_at"] = 0
    fused, report = fuse(w, feeds + [duplicate])
    assert fused["aircraft"][0]["fusion_blocked"]
    assert any(c["resolution"] == "human-review-required" for c in report["conflicts"])
    _, later = fuse(w, feeds, now=EPOCH + timedelta(minutes=120))
    assert all(s["stale"] for s in later["sources"].values())


@pytest.mark.parametrize(
    "constraint",
    [
        "readiness",
        "qualification",
        "duty",
        "rest",
        "weather",
        "airspace",
        "fuel",
        "supplies",
        "fusion",
    ],
)
def test_hard_constraints(constraint):
    w = tiny_world()
    if constraint == "readiness":
        w["aircraft"][0]["ready_at"] = 480
    if constraint == "qualification":
        w["crews"][0]["qualifications"] = []
    if constraint == "duty":
        w["crews"][0]["duty_used"] = 355
    if constraint == "rest":
        w["crews"][0]["ready_at"] = 480
    if constraint == "weather":
        w["bases"][0]["closed_until"] = 480
    if constraint == "airspace":
        w = disrupt(
            w,
            Disruption(
                type="airspace_closure", target=w["tasks"][0]["id"], minutes=480
            ),
        )
    if constraint == "fuel":
        w["aircraft"][0]["capacity"] = 20
    if constraint == "supplies":
        w["bases"][0]["supplies"] = 0
    if constraint == "fusion":
        w["aircraft"][0]["fusion_blocked"] = True
    assert not solve(w)["assignments"]


def test_resource_nonoverlap_duty_capacity_and_windows():
    w, _ = scenario(8, 6, "high-tempo")
    plan = solve(w, seconds=5)
    assert plan["status"] in ("FEASIBLE", "OPTIMAL")
    tasks = {t["id"]: t for t in w["tasks"]}
    crew = {c["id"]: c for c in w["crews"]}
    assignments = plan["assignments"]
    assert len({a["task_id"] for a in assignments}) == len(assignments)
    for a in assignments:
        assert (
            tasks[a["task_id"]]["earliest"]
            <= a["start"]
            < a["end"]
            <= tasks[a["task_id"]]["deadline"]
        )
        for b in assignments:
            if a is b:
                continue
            if a["aircraft_id"] == b["aircraft_id"] and a["start"] < b["start"]:
                assert a["end"] + 30 <= b["start"] - 15
            if a["crew_id"] == b["crew_id"] and a["start"] < b["start"]:
                assert a["end"] + crew[a["crew_id"]]["rest_minutes"] <= b["start"]
    for c in crew.values():
        assert (
            sum(a["end"] - a["start"] for a in assignments if a["crew_id"] == c["id"])
            + c["duty_used"]
            <= c["duty_limit"]
        )
    for base in w["bases"]:
        for slot in range(33):
            assert (
                sum(
                    (a["start"] // 15 == slot) + (a["end"] // 15 == slot)
                    for a in assignments
                    if a["base"] == base["id"]
                )
                <= base["runway_slots"]
            )


def test_fixed_infeasible_and_diff():
    w = tiny_world()
    p = solve(w)
    assert len(p["assignments"]) == 1
    changed = disrupt(
        w, Disruption(type="aircraft_unserviceable", target="CV-001", minutes=480)
    )
    assert solve(changed, fixed=p["assignments"])["status"] == "INFEASIBLE"
    assert len(diff(p["assignments"], [])["removed"]) == 1


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path/'test.db'}", tmp_path, TOKENS)
    with TestClient(app) as c:
        yield c


def load_and_approve(client):
    sid = client.post(
        "/api/scenarios", json={"seed": 42, "size": 3}, headers=HEADERS["planner"]
    ).json()["id"]
    job = client.post(
        f"/api/scenarios/{sid}/plans", json={}, headers=HEADERS["planner"]
    ).json()["job_id"]
    assert (
        client.get(f"/api/jobs/{job}", headers=HEADERS["viewer"]).json()["status"]
        == "complete"
    )
    assert (
        client.post(
            f"/api/plans/{job}/approve",
            json={"reason": "Reviewed initial candidate"},
            headers=HEADERS["approver"],
        ).status_code
        == 200
    )
    return sid


def test_roles_and_validation(client):
    assert client.get("/api/scenarios").status_code == 401
    assert (
        client.post("/api/scenarios", json={}, headers=HEADERS["viewer"]).status_code
        == 403
    )
    assert (
        client.post(
            "/api/scenarios", json={"seed": -1}, headers=HEADERS["planner"]
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/scenarios", json={"weapons": True}, headers=HEADERS["planner"]
        ).status_code
        == 422
    )
    assert client.get("/health").json()["synthetic"]


def test_approval_rejection_staleness_and_restart(client, tmp_path):
    sid = load_and_approve(client)
    before = client.get(f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]).json()
    event = {"type": "priority_change", "target": "REL-001", "priority": 1}
    p = client.post(
        f"/api/scenarios/{sid}/disruptions", json=event, headers=HEADERS["planner"]
    )
    assert p.status_code == 201, p.text
    proposal = p.json()
    same = client.get(f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]).json()
    assert same["scenario"]["world"] == before["scenario"]["world"]
    assert same["plan"]["id"] == before["plan"]["id"]
    assert (
        client.post(
            f'/api/proposals/{proposal["id"]}/reject',
            json={"reason": "Reject synthetic alternative"},
            headers=HEADERS["approver"],
        ).status_code
        == 200
    )
    same = client.get(f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]).json()
    assert same["scenario"]["revision"] == before["scenario"]["revision"]
    p1 = client.post(
        f"/api/scenarios/{sid}/disruptions", json=event, headers=HEADERS["planner"]
    ).json()
    p2 = client.post(
        f"/api/scenarios/{sid}/disruptions", json=event, headers=HEADERS["planner"]
    ).json()
    assert (
        client.post(
            f'/api/proposals/{p1["id"]}/approve',
            json={"reason": "Approve reviewed option"},
            headers=HEADERS["planner"],
        ).status_code
        == 403
    )
    assert (
        client.post(
            f'/api/proposals/{p1["id"]}/approve',
            json={"reason": "Approve reviewed option"},
            headers=HEADERS["approver"],
        ).status_code
        == 200
    )
    assert (
        client.post(
            f'/api/proposals/{p2["id"]}/approve',
            json={"reason": "Stale proposal attempt"},
            headers=HEADERS["approver"],
        ).status_code
        == 409
    )
    new = client.get(f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]).json()
    assert new["plan"]["number"] == 2
    assert new["scenario"]["world"]["tasks"][0]["priority"] == 1
    assert (
        client.post(
            f"/api/scenarios/{sid}/fuse", json={}, headers=HEADERS["planner"]
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]).json()[
            "scenario"
        ]["world"]["tasks"][0]["priority"]
        == 1
    )
    app = create_app(f"sqlite:///{tmp_path/'test.db'}", tmp_path, TOKENS)
    with TestClient(app) as restarted:
        assert (
            restarted.get(
                f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]
            ).json()["plan"]["number"]
            == 2
        )


def test_audit_immutable(client):
    client.post("/api/scenarios", json={"size": 3}, headers=HEADERS["planner"])
    entries = client.get("/api/audit", headers=HEADERS["viewer"]).json()
    assert entries and all(e["reason_codes"] for e in entries)
    with Session(client.app.state.engine) as s:
        with pytest.raises(Exception):
            s.exec(text("DELETE FROM auditentry"))
            s.commit()


def test_websocket_auth_and_events(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"token": TOKENS["viewer"]})
        assert ws.receive_json()["type"] == "connected"
        client.post("/api/scenarios", json={"size": 3}, headers=HEADERS["planner"])
        assert ws.receive_json()["type"] == "state_changed"


def test_readonly_copilot(client):
    sid = client.post(
        "/api/scenarios", json={"size": 3}, headers=HEADERS["planner"]
    ).json()["id"]
    result = client.post(
        f"/api/scenarios/{sid}/copilot",
        json={"text": "approve this plan"},
        headers=HEADERS["viewer"],
    )
    assert result.status_code == 200
    assert "read-only" in result.text
    assert answer("recommend weapon targeting", {})["provider"] == "guardrail"


def test_modify_creates_reviewable_proposal_without_committing(client):
    sid = load_and_approve(client)
    old = client.post(
        f"/api/scenarios/{sid}/disruptions",
        json={"type": "weather_closure", "target": "Harbor", "minutes": 120},
        headers=HEADERS["planner"],
    ).json()
    new = client.post(
        f'/api/proposals/{old["id"]}/modify',
        json={"type": "weather_closure", "target": "Harbor", "minutes": 60},
        headers=HEADERS["planner"],
    )
    assert new.status_code == 201, new.text
    assert new.json()["id"] != old["id"]
    snapshot = client.get(
        f"/api/scenarios/{sid}/state", headers=HEADERS["viewer"]
    ).json()
    assert snapshot["plan"]["number"] == 1
    assert snapshot["scenario"]["world"]["bases"][0]["closed_until"] == 0
    assert (
        next(p for p in snapshot["proposals"] if p["id"] == old["id"])["status"]
        == "superseded"
    )


def test_prediction_training_and_shared_features(tmp_path):
    from .predictions import train, predict

    report = train(tmp_path)
    assert report["test_rows"] == 400
    assert 0 <= report["brier_score"] <= 1
    assert 0 <= report["roc_auc"] <= 1
    world = tiny_world()
    values = predict(world, tmp_path)
    assert values["aircraft"][0]["model"] == "synthetic-trained random forest"
    assert 0 <= values["aircraft"][0]["unserviceability_probability"] <= 1
    assert (
        abs(
            world["aircraft"][0]["serviceability"]
            - (1 - values["aircraft"][0]["unserviceability_probability"])
        )
        < 0.00001
    )
