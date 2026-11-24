from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import analyze_dataset, apply_measurement, enumeration_check, recommend_measurements  # noqa: E402
from app.server import AppHandler  # noqa: E402

SEED = json.loads((ROOT / "app" / "data" / "seed.json").read_text(encoding="utf-8"))


def tiny_correlation_dataset():
    return {
        "name": "correlation trap",
        "metrics": [
            {"id": "cost", "name": "成本", "direction": "min", "unit": "", "scale": [0, 10]},
            {"id": "energy", "name": "能耗", "direction": "min", "unit": "", "scale": [0, 20]},
            {"id": "yield", "name": "收率", "direction": "max", "unit": "", "scale": [70, 100]},
        ],
        "weight_ranges": {
            "cost": [0.2, 0.5],
            "energy": [0.2, 0.5],
            "yield": [0.2, 0.5],
        },
        "alternatives": [
            {"id": "A", "name": "A"},
            {"id": "B", "name": "B"},
        ],
        "scenarios": [
            {
                "id": "S1",
                "values": {
                    # A's low cost occurs together with high energy/low yield.
                    "A": {"cost": 4, "energy": 18, "yield": 85},
                    "B": {"cost": 5, "energy": 12, "yield": 90},
                },
            },
            {
                "id": "S2",
                "values": {
                    # A's low energy/high yield occurs with high cost.
                    "A": {"cost": 6, "energy": 10, "yield": 95},
                    "B": {"cost": 5, "energy": 12, "yield": 90},
                },
            },
        ],
    }


class EngineTests(unittest.TestCase):
    def test_seed_has_required_candidate_sets(self):
        result = analyze_dataset(SEED)
        by_id = {a["id"]: a for a in result["alternatives"]}

        self.assertEqual(by_id["P1"]["decision_class"], "dominated")
        self.assertIn("P2", by_id["P1"]["dominated_by"])
        self.assertEqual(by_id["P7"]["decision_class"], "dominated")
        self.assertIn("P5", by_id["P7"]["dominated_by"])

        for pid in ["P2", "P3", "P4", "P5", "P6"]:
            self.assertEqual(by_id[pid]["decision_class"], "possible_effective", pid)
            self.assertIsNotNone(by_id[pid]["optimal_witness"], pid)

        self.assertEqual(by_id["P8"]["decision_class"], "insufficient_evidence")
        self.assertEqual(by_id["P8"]["reason_code"], "missing_joint_evidence")
        self.assertEqual(result["possible_winner_ids"], ["P2", "P3", "P4", "P5", "P6"])

    def test_fully_equivalent_alternatives_are_not_dominated(self):
        result = analyze_dataset(SEED)
        by_id = {a["id"]: a for a in result["alternatives"]}
        self.assertEqual(by_id["P2"]["equivalent_to"], ["P6"])
        self.assertEqual(by_id["P6"]["equivalent_to"], ["P2"])
        self.assertEqual(result["equivalent_groups"], [["P2", "P6"]])
        pair = next(p for p in result["pairs"] if {p["a"], p["b"]} == {"P2", "P6"})
        self.assertTrue(pair["equivalent"])
        self.assertFalse(pair["a_dominates_b"])
        self.assertFalse(pair["b_dominates_a"])

    def test_joint_scenarios_prevent_impossible_worst_case_dominance(self):
        data = tiny_correlation_dataset()
        result = analyze_dataset(data)
        pair = result["pairs"][0]
        self.assertEqual(pair["relation"], "tradeoff")
        # A is cheaper in S1, blocking B-dominates-A.
        s1 = next(row for row in pair["rows"] if row["scenario_id"] == "S1")
        cost_cell = next(c for c in s1["metrics"] if c["metric_id"] == "cost")
        self.assertEqual(cost_cell["comparison"], "a_better")
        # A is more energy efficient and higher yielding in S2.
        s2 = next(row for row in pair["rows"] if row["scenario_id"] == "S2")
        energy_cell = next(c for c in s2["metrics"] if c["metric_id"] == "energy")
        yield_cell = next(c for c in s2["metrics"] if c["metric_id"] == "yield")
        self.assertEqual(energy_cell["comparison"], "a_better")
        self.assertEqual(yield_cell["comparison"], "a_better")

    def test_every_returned_weight_witness_is_valid(self):
        result = analyze_dataset(SEED)
        metric_ids = [m["id"] for m in SEED["metrics"]]
        for alt in result["alternatives"]:
            w = alt.get("optimal_witness")
            if not w:
                continue
            weights = [w["weights"][mid] for mid in metric_ids]
            self.assertAlmostEqual(sum(weights), 1.0, places=6)
            for mid, value in zip(metric_ids, weights):
                lo, hi = result["weight_ranges"][mid]
                self.assertGreaterEqual(value, lo - 1e-6)
                self.assertLessEqual(value, hi + 1e-6)
            top_score = w["scores"][0]
            self.assertEqual(top_score["alternative_id"], alt["id"])

    def test_incompatible_weight_ranges_are_reported_not_hidden(self):
        options = {"weight_ranges": {
            "cost": [0.8, 1.0],
            "energy": [0.8, 1.0],
            "yield": [0.8, 1.0],
        }}
        result = analyze_dataset(SEED, options)
        self.assertEqual(result["weight_state"], "infeasible")
        self.assertIsNotNone(result["weight_error"])
        self.assertEqual(result["possible_winner_ids"], [])

    def test_contradictory_hard_thresholds_are_detected(self):
        options = {"thresholds": SEED["threshold_presets"]["contradiction"]}
        result = analyze_dataset(SEED, options)
        self.assertEqual(result["eligible_ids"], [])
        self.assertIsNotNone(result["threshold_contradiction"])
        self.assertEqual(
            result["threshold_contradiction"]["type"],
            "no_joint_point_passes_all_locks",
        )
        self.assertTrue(result["threshold_contradiction"]["relaxation_candidates"])

    def test_normal_hard_threshold_shortlist(self):
        options = {"thresholds": SEED["threshold_presets"]["normal"]}
        result = analyze_dataset(SEED, options)
        self.assertEqual(set(result["eligible_ids"]), {"P2", "P5", "P6"})
        self.assertEqual(set(result["possible_winner_ids"]), {"P2", "P5", "P6"})

    def test_measurement_recommendation_and_application(self):
        rec = recommend_measurements(SEED)
        ids = [r["measurement_id"] for r in rec["recommendations"]]
        self.assertIn("M2", ids)
        m2 = next(r for r in rec["recommendations"] if r["measurement_id"] == "M2")
        self.assertGreater(m2["expected_reduction"], 0)
        self.assertGreaterEqual(m2["upside_reduction"], m2["expected_reduction"])

        applied = apply_measurement(
            SEED,
            {"measurement_id": "M2", "outcome_id": "penalty"},
        )
        after = applied["after"]
        self.assertNotIn("P4", after["possible_winner_ids"])
        self.assertIn("P4", applied["diff"]["removed_winner_ids"])
        self.assertLess(after["ambiguity"]["score"], applied["before"]["ambiguity"]["score"])
        self.assertIn("歧义指数", applied["diff"]["explanation"])

    def test_joint_measurement_resolves_evidence_gap(self):
        applied = apply_measurement(
            SEED,
            {"measurement_id": "M4", "outcome_id": "p8_penalty"},
        )
        p8 = next(a for a in applied["after"]["alternatives"] if a["id"] == "P8")
        self.assertEqual(p8["evidence_complete"], True)
        self.assertEqual(p8["decision_class"], "dominated")
        self.assertEqual(p8["dominated_by"], ["P4"])

    def test_custom_marginal_interval_narrows_but_does_not_fabricate_joint_evidence(self):
        applied = apply_measurement(
            SEED,
            {"custom": {
                "alternative_id": "P8",
                "metric_id": "cost",
                "value": 4.1,
                "tolerance": 0.2,
                "cost": 4,
            }},
        )
        p8 = next(a for a in applied["after"]["alternatives"] if a["id"] == "P8")
        self.assertEqual(p8["decision_class"], "insufficient_evidence")
        self.assertIn("只有边际区间", applied["applied_record"]["limitation"])

    def test_custom_measurement_that_rules_out_every_scenario_is_rejected(self):
        with self.assertRaises(Exception):
            apply_measurement(
                SEED,
                {"custom": {
                    "alternative_id": "P2",
                    "metric_id": "cost",
                    "value": 999,
                    "tolerance": 0.01,
                    "cost": 1,
                }},
            )

    def test_small_enumeration_matches_exact_candidate_set(self):
        report = enumeration_check(SEED, grid_n=150)
        self.assertTrue(report["sound"])
        self.assertTrue(report["grid_covers_exact_set"])
        self.assertEqual(
            set(report["brute_force_winner_ids"]),
            set(report["exact_winner_ids"]),
        )
        self.assertGreater(report["score_evaluations"], 1000)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def get(self, path):
        with request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.read()

    def post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_static_and_seed(self):
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, html = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("多指标工艺决策台".encode("utf-8"), html)

    def test_analyze_api(self):
        status, payload = self.post("/api/analyze", {})
        self.assertEqual(status, 200)
        self.assertIn("P2", payload["possible_winner_ids"])

    def test_bad_dataset_returns_400(self):
        try:
            self.post("/api/analyze", {"dataset": {"nope": True}})
            self.fail("expected HTTP 400")
        except Exception as exc:
            self.assertIn("400", str(exc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
