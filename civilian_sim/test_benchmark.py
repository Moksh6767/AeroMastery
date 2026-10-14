"""Checks paired design, statistics and generated chart without a full run."""
import contextlib
import io
import math
import unittest
import xml.etree.ElementTree as ET

from .benchmark import run_benchmark, scaling_svg, summarize


class BenchmarkTests(unittest.TestCase):
    def test_statistics(self):
        summary = summarize([1., 2., 3.])
        self.assertEqual(summary["mean"], 2)
        self.assertEqual(summary["sample_variance"], 1)
        self.assertAlmostEqual(summary["ci95_mean"][0], 2 - 1.96 / math.sqrt(3))
        self.assertEqual(summary["p50"], 2)
        self.assertAlmostEqual(summary["p95"], 2.9)
        self.assertIsNone(summarize([1])["ci95_mean"])

    def test_pairing_previous_plans_and_chart(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rows, aggregates = run_benchmark(2, [20], 42)
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(aggregates), 6)
        for seed in (42, 43):
            for stage in ("initial", "replan"):
                selected = [r for r in rows if r["seed"] == seed and r["stage"] == stage]
                self.assertEqual(len({r["world_sha256"] for r in selected}), 1)
                self.assertEqual({r["robustness_samples"] for r in selected}, {32})
            for method in ("manual", "priority", "solver"):
                pair = [r for r in rows if r["seed"] == seed and r["method"] == method]
                self.assertEqual(pair[0]["assignments_sha256"], pair[1]["previous_assignments_sha256"])
                self.assertNotEqual(pair[0]["world_sha256"], pair[1]["world_sha256"])
        self.assertTrue(all(a["metrics"]["latency_ms"]["n"] == 2 for a in aggregates))
        svg = ET.fromstring(scaling_svg(aggregates, [20]))
        self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")


if __name__ == "__main__":
    unittest.main()