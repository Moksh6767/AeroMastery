"""Reproducible synthetic civilian benchmark; standard library only.

Run from the workspace root with ``python -m civilian_sim.benchmark``.
Small --scenarios values are supported for smoke tests, not benchmark claims.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time
from xml.sax.saxutils import escape

from . import model, planner


METHODS = ("manual", "priority", "solver")
STAGES = ("initial", "replan")
METRICS = ("latency_ms", "coverage", "p1_coverage", "risk", "disruption",
           "fuel", "robustness", "slack")
SAMPLES = 32
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "runtime" / "benchmarks.json"


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def percentile(values, probability):
    """Linear interpolation between ordered observations (inclusive endpoints)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(values):
    count = len(values)
    mean = statistics.mean(values)
    variance = statistics.variance(values) if count > 1 else None
    radius = 1.96 * math.sqrt(variance / count) if variance is not None else None
    return {"n": count, "mean": mean, "sample_variance": variance,
            "ci95_mean": [mean - radius, mean + radius] if radius is not None else None,
            "p50": percentile(values, .5), "p95": percentile(values, .95)}


def measure(world, method, previous):
    # Copies and validation are outside the timed region, for every method.
    local_world = deepcopy(world)
    local_previous = deepcopy(previous)
    started = time.perf_counter_ns()
    plan = planner.solve(local_world, method=method, previous=local_previous,
                         samples=SAMPLES, diagnose=False)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if local_world != world or local_previous != previous:
        raise RuntimeError("Planner mutated its input world or previous assignments")
    metrics = {key: plan["metrics"][key] for key in METRICS if key != "latency_ms"}
    metrics["latency_ms"] = elapsed_ms
    if any(not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in metrics.values()):
        raise RuntimeError("Planner returned a missing or non-finite metric")
    return plan, metrics


def run_benchmark(scenarios, sizes, seed):
    rows = []
    for size in sizes:
        for index in range(scenarios):
            world = model.generate(seed=seed + index, size=size)
            active_ids = {a["aircraft_id"] for a in world["active"]}
            eligible = [a for a in world["aircraft"] if a["id"] not in active_ids]
            if not eligible:
                raise RuntimeError("No inactive aircraft available for validated delay")
            rng = random.Random(world["seed"] + 104729)
            event = model.validate_scenario(
                {"type": "aircraft_delay", "aircraft_id": rng.choice(eligible)["id"],
                 "minutes": rng.choice([120, 150, 180, 210, 240])}, world)
            disrupted = deepcopy(world)
            model.apply_scenario(disrupted, event)
            if disrupted == world:
                raise RuntimeError("Aircraft delay did not change the world")
            world_hashes = {"initial": digest(world), "replan": digest(disrupted)}
            previous = {}
            # Rotate order to reduce systematic first/last method timing bias.
            offset = index % len(METHODS)
            order = METHODS[offset:] + METHODS[:offset]
            for stage in STAGES:
                current_world = world if stage == "initial" else disrupted
                for method in order:
                    old = [] if stage == "initial" else previous[method]
                    plan, metrics = measure(current_world, method, old)
                    rows.append({"size": size, "seed": world["seed"], "stage": stage,
                                 "method": method, "method_order": list(order),
                                 "task_count": len(world["tasks"]),
                                 "p1_count": sum(t["priority"] == 1 for t in world["tasks"]),
                                 "active_aircraft_count": len(active_ids), "scenario": event,
                                 "world_sha256": world_hashes[stage],
                                 "previous_assignment_count": len(old),
                                 "previous_assignments_sha256": digest(old),
                                 "assignment_count": len(plan["assignments"]),
                                 "assignments_sha256": digest(plan["assignments"]),
                                 "robustness_samples": plan["metrics"]["samples"],
                                 "robustness_ci95_wilson": plan["metrics"]["robustness_ci"],
                                 "metrics": metrics})
                    if stage == "initial":
                        previous[method] = plan["assignments"]
        print(f"Completed fleet size {size}: {scenarios} paired scenarios", file=sys.stderr)
    aggregates = []
    for size in sizes:
        for stage in STAGES:
            for method in METHODS:
                selected = [row for row in rows if (row["size"], row["stage"], row["method"])
                            == (size, stage, method)]
                aggregates.append({"size": size, "stage": stage, "method": method,
                                   "metrics": {key: summarize([row["metrics"][key]
                                                              for row in selected])
                                               for key in METRICS}})
    return rows, aggregates


def scaling_svg(aggregates, sizes):
    """Two linear-axis panels showing externally measured mean solve latency."""
    sizes = sorted(sizes)
    colors = {"manual": "#2563eb", "priority": "#15803d", "solver": "#b91c1c"}
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="490" '
           'viewBox="0 0 1000 490" role="img" aria-labelledby="title description">',
           '<title id="title">Synthetic civilian planning latency scaling</title>',
           '<desc id="description">Mean externally timed solve latency by fleet size. '
           'Error bars are normal-approximation 95% confidence intervals of the mean.</desc>',
           '<rect width="1000" height="490" fill="white"/>',
           '<g font-family="sans-serif" font-size="12" fill="#111827">']

    def text(x, y, value, anchor="middle"):
        svg.append(f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}">{escape(str(value))}</text>')

    text(500, 25, "Synthetic civilian benchmark: externally measured solve latency")
    for panel, stage in enumerate(STAGES):
        left, top, width, height = 75 + panel * 495, 80, 390, 280
        lookup = {(a["size"], a["method"]): a["metrics"]["latency_ms"]
                  for a in aggregates if a["stage"] == stage}
        maximum = max((value["ci95_mean"] or [value["mean"], value["mean"]])[1]
                      for value in lookup.values()) * 1.12 or 1.

        def x(size):
            return left + (width / 2 if sizes[-1] == sizes[0]
                           else width * (size - sizes[0]) / (sizes[-1] - sizes[0]))

        def y(value):
            return top + height - height * value / maximum

        text(left + width / 2, 55, stage.capitalize())
        text(left, 72, "Latency (ms)", "start")
        for tick in range(6):
            value = maximum * tick / 5
            yy = y(value)
            svg.append(f'<path d="M {left} {yy:.2f} H {left + width}" stroke="#e5e7eb"/>')
            text(left - 8, yy + 4, f"{value:.2f}", "end")
        svg.append(f'<path d="M {left} {top} V {top + height} H {left + width}" '
                   'fill="none" stroke="#374151"/>')
        for size in sizes:
            text(x(size), top + height + 22, size)
        text(left + width / 2, top + height + 46, "Fleet size (aircraft; tasks ≈ 1.4 × fleet)")
        for method in METHODS:
            points = " ".join(f'{x(size):.2f},{y(lookup[size, method]["mean"]):.2f}' for size in sizes)
            svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[method]}" stroke-width="2"/>')
            for size in sizes:
                value = lookup[size, method]
                xx, yy = x(size), y(value["mean"])
                if value["ci95_mean"] is not None:
                    low, high = value["ci95_mean"]
                    # Ratios/latencies are not clipped in JSON; only SVG's zero-axis is clipped.
                    ylow, yhigh = y(max(0, low)), y(high)
                    svg.append(f'<path d="M {xx:.2f} {ylow:.2f} V {yhigh:.2f} '
                               f'M {xx-4:.2f} {ylow:.2f} H {xx+4:.2f} '
                               f'M {xx-4:.2f} {yhigh:.2f} H {xx+4:.2f}" stroke="{colors[method]}"/>')
                svg.append(f'<circle cx="{xx:.2f}" cy="{yy:.2f}" r="4" fill="{colors[method]}"/>')
    for index, method in enumerate(METHODS):
        xx = 220 + index * 230
        svg.append(f'<path d="M {xx} 435 h 25" stroke="{colors[method]}" stroke-width="3"/>')
        text(xx + 33, 439, method, "start")
    text(500, 472, "Bars: normal 95% CI of mean (display clipped at zero); solver is a heuristic, not exact.")
    svg.append("</g></svg>")
    return "\n".join(svg)


def metadata(args, duration):
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "implementation": platform.python_implementation(),
        "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "benchmark_elapsed_seconds": duration,
        "scenarios_per_size": args.scenarios, "sizes": args.sizes,
        "seed_range_inclusive": [args.seed, args.seed + args.scenarios - 1],
        "run_kind": "benchmark" if args.scenarios >= 100 else "smoke_only",
        "meets_100_scenarios_per_size": args.scenarios >= 100,
        "methods": {"manual": "Manual-style input-order greedy; not measured human planning",
                    "priority": "Priority-first greedy ordered by priority then deadline",
                    "solver": "Six-order multi-start constructive heuristic; not an exact solver or optimality proof"},
        "weights": dict(planner.DEFAULT_WEIGHTS), "robustness_samples_per_solve": SAMPLES,
        "diagnose": False, "stages": list(STAGES),
        "units": {"size": "aircraft; crews equal fleet size, task count round(1.4 * size)",
                  "latency_ms": "milliseconds", "coverage": "fraction of pending tasks scheduled",
                  "p1_coverage": "fraction of priority-1 tasks scheduled (1 if none)",
                  "risk": "mean synthetic failure probability conditional on scheduled flights (0 if none)",
                  "disruption": "fraction of own previous tasks dropped or changed aircraft/crew/start (0 if no previous)",
                  "fuel": "sum of scheduled synthetic fuel units; not calibrated physical units",
                  "robustness": "fraction of 32 fixed-plan futures satisfying all P1 tasks; vacuously 1 with no P1",
                  "slack": "mean scheduled task deadline minus completion, in minutes (0 if none)"},
        "statistics": {
            "unit_of_observation": "one seeded world, one method, one stage; equal weight per scenario",
            "sample_variance": "unbiased sample variance with denominator n-1, in squared metric units",
            "ci95_mean": "Normal approximation: mean +/- 1.96 * sqrt(sample_variance/n); not Student t; not clipped",
            "small_sample": "Variance and CI null for n=1; normal approximation unreliable for small n or rare/bounded outcomes",
            "percentiles": "Linear interpolation at (n-1)*p in sorted observations",
            "dependence": "Methods and stages paired on identical seeds/worlds; sizes reuse seeds. Marginal CIs are not paired-difference significance tests",
            "robustness_ci95_wilson": "Per-row binomial Wilson interval over 32 synthetic futures; distinct from aggregate CI across worlds"},
        "timing_notes": [
            "perf_counter_ns measured immediately around planner.solve; planner internal latency is not used",
            "Includes construction, scoring and 32 robustness futures; excludes world generation, copies, hashes, validation, aggregation and file output",
            "All methods use samples=32, diagnose=False and identical default weights",
            "One measured call per seed/method/stage; no warmup, retries, discarded observations or parallel workers",
            "Method execution order rotates by scenario index; all initial solves precede replans",
            "Wall-clock timings depend on host load, Python/runtime state and hardware; no runtime guarantee"],
        "assumptions": [
            "Standalone synthetic civilian model only; no original repository datasets or operational calibration",
            "Each size uses the identical seeded world for every method; inputs deep-copied outside timing",
            "One seeded inactive aircraft chosen independently of plans; validated delay is 120-240 minutes in 30-minute steps",
            "Replan occurs before dispatch at model time zero; no active flights, elapsed execution or committed initial assignments",
            "Every method replans the same disrupted world using only its own previous assignments",
            "32 common seeded robustness futures per solve; fixed-plan P1 serviceability/weather check, not full recourse or delayed-resource simulation",
            "Coverage, conditional risk, total fuel and slack must be interpreted together; low fuel/risk can reflect fewer scheduled tasks"],
        "source_sha256": {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for module in (model, planner)},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=100, help="scenarios per size (default: 100; smaller is smoke only)")
    parser.add_argument("--sizes", type=int, nargs="+", default=[20, 100, 300], help="fleet sizes, each between 3 and 300")
    parser.add_argument("--seed", type=int, default=42, help="first seed in consecutive inclusive seed range")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON output; SVG uses same stem alongside it")
    args = parser.parse_args(argv)
    if args.scenarios < 1:
        parser.error("--scenarios must be at least 1")
    if not 0 <= args.seed <= args.seed + args.scenarios - 1 <= 2**31 - 1:
        parser.error("seed range must fit integers 0 through 2147483647")
    if len(set(args.sizes)) != len(args.sizes) or any(not 3 <= size <= 300 for size in args.sizes):
        parser.error("--sizes must be distinct integers between 3 and 300")
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".json":
        parser.error("--output must have a .json extension")
    if args.scenarios < 100:
        print("WARNING: fewer than 100 scenarios per size; smoke test only.", file=sys.stderr)
    started = time.perf_counter()
    rows, aggregates = run_benchmark(args.scenarios, args.sizes, args.seed)
    report = {"schema_version": 1, "metadata": metadata(args, time.perf_counter() - started),
              "aggregates": aggregates, "rows": rows}
    chart = output.with_suffix(".svg")
    report["metadata"]["artifacts"] = {"json": str(output), "scaling_svg": str(chart)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    chart.write_text(scaling_svg(aggregates, args.sizes) + "\n", encoding="utf-8")
    print(f"Saved {len(rows)} measured rows to {output}")
    print(f"Saved scaling chart to {chart}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())