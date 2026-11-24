"""Independent small-scale lattice enumeration used to cross-check witnesses."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .analysis import analyze_dataset, desirability, normalize_options


def _simplex_weights(metric_count: int, grid_n: int):
    if metric_count == 1:
        yield [1.0]
        return

    def recurse(remaining_metrics: int, remaining_units: int, prefix: List[int]):
        if remaining_metrics == 1:
            yield prefix + [remaining_units]
            return
        for units in range(remaining_units + 1):
            yield from recurse(remaining_metrics - 1, remaining_units - units, prefix + [units])

    for units in recurse(metric_count, grid_n, []):
        yield [u / grid_n for u in units]


def enumeration_check(
    dataset: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
    grid_n: int = 180,
) -> Dict[str, Any]:
    """Brute-force every lattice weight and joint scenario.

    This is intentionally independent of the basis-feasibility witness path.
    It verifies that every grid-observed winner is included in the exact
    existential set.  A fine grid can miss a razor-thin feasible weight wedge,
    so equality is reported rather than assumed.
    """
    analysis = analyze_dataset(dataset, options)
    opts = normalize_options(dataset, options)
    metrics = dataset["metrics"]
    mids = [m["id"] for m in metrics]
    ranges = opts["weight_ranges"]
    locked_ids = set(analysis["eligible_ids"])

    evaluated_weights = []
    for w in _simplex_weights(len(metrics), grid_n):
        if all(ranges[mid][0] - 1e-10 <= w[i] <= ranges[mid][1] + 1e-10 for i, mid in enumerate(mids)):
            evaluated_weights.append(w)

    brute_winners = set()
    evaluations = 0
    score_rows = []
    for scenario in dataset.get("scenarios", []):
        eligible_in_scenario = [
            aid
            for aid in locked_ids
            if aid in scenario.get("values", {}) and all(mid in scenario["values"][aid] for mid in mids)
        ]
        if not eligible_in_scenario:
            continue
        utilities = {
            aid: [desirability(float(scenario["values"][aid][mid]), m) for mid, m in zip(mids, metrics)]
            for aid in eligible_in_scenario
        }
        for w in evaluated_weights:
            scored = []
            for aid in eligible_in_scenario:
                score = sum(w[i] * utilities[aid][i] for i in range(len(mids)))
                evaluations += 1
                scored.append((score, aid))
            if scored:
                best_score = max(score for score, _ in scored)
                # Exact equivalents and lattice ties are all part of the
                # brute-force winner set.
                for score, aid in scored:
                    if abs(score - best_score) <= 1e-9:
                        brute_winners.add(aid)
                        score_rows.append((aid, scenario["id"], tuple(round(x, 6) for x in w)))

    exact_winners = set(analysis["possible_winner_ids"])
    missing_from_exact = sorted(brute_winners - exact_winners)
    grid_only_not_found = sorted(exact_winners - brute_winners)
    # Grid may miss a narrow wedge, but it must never observe a winner the
    # exact path says is impossible.
    sound = not missing_from_exact
    sample_witnesses = []
    for aid, sid, w in score_rows[:12]:
        sample_witnesses.append({"alternative_id": aid, "scenario_id": sid, "weights": list(w)})

    # Independent pair enumeration count.
    pair_violations = 0
    for pair in analysis["pairs"]:
        for row in pair["rows"]:
            for cell in row["metrics"]:
                if cell["comparison"] == "b_better":
                    pair_violations += 1

    return {
        "grid_n": grid_n,
        "weight_points_in_preference_range": len(evaluated_weights),
        "scenario_count": len(dataset.get("scenarios", [])),
        "alternative_count": len(dataset.get("alternatives", [])),
        "score_evaluations": evaluations,
        "brute_force_winner_ids": sorted(brute_winners),
        "exact_winner_ids": sorted(exact_winners),
        "grid_winners_missing_from_exact": missing_from_exact,
        "exact_winners_not_hit_by_grid": grid_only_not_found,
        "sound": sound,
        "grid_covers_exact_set": not grid_only_not_found,
        "pair_count": len(analysis["pairs"]),
        "blocking_or_strict_cells": pair_violations,
        "sample_grid_witnesses": sample_witnesses,
        "statement": "已枚举全部联合场景、偏好范围内的单纯形网格权重和候选方案；网格见证必须是精确候选集合的子集。",
    }
