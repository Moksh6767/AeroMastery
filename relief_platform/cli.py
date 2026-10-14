"""One-command demo seed, training, and measured benchmark generation."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
from sqlmodel import SQLModel, Session, create_engine
from .models import Scenario, PlanVersion, AuditEntry, Disruption
from .scenarios import scenario, fuse, disrupt
from .optimizer import solve, baseline
from .predictions import train, predict
from .storage import save_entities, save_plan
from .models import Reason


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["seed", "train", "benchmark"])
    p.add_argument("--runtime", type=Path, default=Path("relief_platform/runtime"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--scenarios", type=int, default=4)
    args = p.parse_args()
    args.runtime.mkdir(parents=True, exist_ok=True)
    if args.command == "train":
        print(json.dumps(train(args.runtime, args.seed), indent=2))
        return
    if args.command == "seed":
        train(args.runtime, args.seed)
        world, feeds = scenario(args.seed, 9, "baseline")
        world, report = fuse(world, feeds)
        row = Scenario(
            id=uuid4().hex,
            preset="baseline",
            seed=args.seed,
            world=world,
            feeds=feeds,
            fusion=report,
        )
        predicted = deepcopy(world)
        predict(predicted, args.runtime)
        plan = solve(predicted)
        plan["baseline"] = baseline(predicted)
        db = create_engine(f"sqlite:///{args.runtime/'relief.db'}")
        SQLModel.metadata.create_all(db)
        with Session(db, expire_on_commit=False) as s:
            candidate = PlanVersion(
                id=uuid4().hex, scenario_id=row.id, number=0, revision=1, plan=plan
            )
            s.add(row)
            save_entities(s, row)
            s.add(candidate)
            save_plan(s, candidate)
            s.add(
                AuditEntry(
                    actor="seed-script",
                    action="demo_seeded",
                    reason_codes=[Reason.SCENARIO],
                    after={"scenario_id": row.id},
                )
            )
            s.commit()
        print(f"Seeded scenario {row.id}; candidate requires human approval.")
        return
    results = []
    for i in range(args.scenarios):
        preset = ["baseline", "high-tempo", "degraded-weather", "many-aircraft-down"][
            i % 4
        ]
        world, feeds = scenario(args.seed + i, 6, preset)
        world, _ = fuse(world, feeds)
        predict(world, args.runtime)
        optimized = solve(world)
        greedy = baseline(world)
        event = Disruption(
            type="aircraft_unserviceable",
            target=world["aircraft"][0]["id"],
            minutes=240,
        )
        replanned = solve(disrupt(world, event), optimized["assignments"])
        results.append(
            {
                "seed": args.seed + i,
                "preset": preset,
                "fleet_size": 6,
                "solver_status": optimized["status"],
                "optimized": optimized["metrics"],
                "greedy": greedy["metrics"],
                "replanned": replanned["metrics"],
            }
        )
    report = {
        "synthetic": True,
        "scenario_count": len(results),
        "units": {
            "time": "measured seconds",
            "coverage": "fraction",
            "changes": "assignment count",
        },
        "results": results,
    }
    (args.runtime / "benchmarks.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
