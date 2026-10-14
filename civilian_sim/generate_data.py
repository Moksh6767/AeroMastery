"""Export the exact standalone civilian seed as CSVs and a reloadable snapshot."""
import argparse
import csv
import json
from pathlib import Path

from .model import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--size", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "runtime" / "data")
    args = parser.parse_args()
    world = generate(args.seed, args.size)
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("aircraft", "crews", "bases", "tasks"):
        rows = world[name]
        with (args.output / f"civilian_{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows({k: json.dumps(v) if isinstance(v, list) else v for k, v in row.items()} for row in rows)
    (args.output / "scenario.json").write_text(json.dumps(world, indent=2), encoding="utf-8")
    print(f"Exported civilian seed {args.seed}, fleet {args.size} to {args.output.resolve()}")


if __name__ == "__main__":
    main()