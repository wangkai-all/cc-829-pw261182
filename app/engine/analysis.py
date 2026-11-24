"""Core evidence-preserving multi-criteria decision analysis.

The engine never builds a Cartesian product of marginal intervals.  All
dominance and optimality witnesses are evaluated on supplied *joint* scenarios
or after patches that preserve those joint vectors.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .linalg import feasible_weight_point

EPS = 2e-7
CLASS_LABELS = {
    "dominated": "确定被支配",
    "possible_effective": "可能有效",
    "insufficient_evidence": "证据不足",
    "threshold_blocked": "硬阈值排除",
    "preference_excluded": "当前权重范围外",
}


class DatasetError(ValueError):
    pass


def _eps_key() -> float:
    return EPS


def metric_map(dataset: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {m["id"]: m for m in dataset.get("metrics", [])}


def alternative_map(dataset: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {a["id"]: a for a in dataset.get("alternatives", [])}


def scenario_list(dataset: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(dataset.get("scenarios", []))


def desirability(value: float, metric: Dict[str, Any]) -> float:
    low, high = metric.get("scale", [0.0, 1.0])
    if high <= low:
        raise DatasetError(f"指标 {metric.get('id')} 的 scale 上下界无效")
    if metric.get("direction", "min") == "max":
        return (float(value) - low) / (high - low)
    return (high - float(value)) / (high - low)


def direction_better(metric: Dict[str, Any], a: float, b: float, eps: float = EPS) -> int:
    """Return 1 if a is better, -1 if b is better, 0 if equal/within eps."""
    if metric.get("direction", "min") == "max":
        delta = a - b
    else:
        delta = b - a
    if delta > eps:
        return 1
    if delta < -eps:
        return -1
    return 0


def normalize_options(dataset: Dict[str, Any], options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    options = options or {}
    metrics = dataset.get("metrics", [])
    ids = [m["id"] for m in metrics]

    raw_ranges = options.get("weight_ranges", dataset.get("weight_ranges", {}))
    weight_ranges: Dict[str, Tuple[float, float]] = {}
    for mid in ids:
        rng = raw_ranges.get(mid, [0.0, 1.0]) if isinstance(raw_ranges, dict) else [0.0, 1.0]
        try:
            lo, hi = float(rng[0]), float(rng[1])
        except (TypeError, ValueError, IndexError):
            lo, hi = 0.0, 1.0
        lo = max(0.0, min(1.0, lo))
        hi = max(0.0, min(1.0, hi))
        if lo > hi:
            lo, hi = hi, lo
        weight_ranges[mid] = (lo, hi)

    raw_thresholds = options.get("thresholds", dataset.get("thresholds", [])) or []
    thresholds: List[Dict[str, Any]] = []
    valid_ids = set(ids)
    for th in raw_thresholds:
        if not isinstance(th, dict) or th.get("metric_id") not in valid_ids:
            continue
        if not th.get("locked", False):
            continue
        try:
            value = float(th["value"])
        except (KeyError, TypeError, ValueError):
            continue
        op = th.get("op")
        if op not in ("<=", ">=", "<", ">"):
            metric = metric_map(dataset)[th["metric_id"]]
            op = ">=" if metric.get("direction") == "max" else "<="
        thresholds.append(
            {
                "metric_id": th["metric_id"],
                "op": op,
                "value": value,
                "locked": True,
                "label": th.get("label", ""),
            }
        )

    return {"weight_ranges": weight_ranges, "thresholds": thresholds}


def has_joint_values(alt_id: str, dataset: Dict[str, Any]) -> Tuple[bool, List[str]]:
    alt = alternative_map(dataset).get(alt_id, {})
    scenarios = scenario_list(dataset)
    if not scenarios:
        return False, ["dataset:no_joint_scenarios"]
    if alt.get("evidence_mode") == "marginal_only":
        return False, ["alternative marked marginal_only"]
    metric_ids = [m["id"] for m in dataset.get("metrics", [])]
    missing: List[str] = []
    for scenario in scenarios:
        values = scenario.get("values", {}).get(alt_id)
        if not isinstance(values, dict):
            missing.append(f"{scenario.get('id')}:row")
            continue
        for mid in metric_ids:
            if mid not in values or values[mid] is None:
                missing.append(f"{scenario.get('id')}:{mid}")
    return not missing, missing


def compute_bounds(dataset: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    metrics = dataset.get("metrics", [])
    result: Dict[str, Dict[str, Any]] = {}
    for alt in dataset.get("alternatives", []):
        aid = alt["id"]
        item: Dict[str, Any] = {"evidence_mode": alt.get("evidence_mode", "joint"), "metrics": {}}
        complete, _ = has_joint_values(aid, dataset)
        if not complete:
            intervals = alt.get("intervals", {}) or {}
            for metric in metrics:
                mid = metric["id"]
                rng = intervals.get(mid)
                if rng:
                    item["metrics"][mid] = {
                        "min": float(rng[0]),
                        "max": float(rng[1]),
                        "source": "marginal_interval",
                        "joint_endpoints": False,
                    }
            item["evidence_mode"] = "marginal_only"
            result[aid] = item
            continue

        for metric in metrics:
            mid = metric["id"]
            points = []
            for scenario in scenario_list(dataset):
                raw = scenario["values"][aid][mid]
                points.append((float(raw), scenario["id"]))
            lo_val, lo_sid = min(points, key=lambda x: x[0])
            hi_val, hi_sid = max(points, key=lambda x: x[0])
            item["metrics"][mid] = {
                "min": lo_val,
                "max": hi_val,
                "min_scenario": lo_sid,
                "max_scenario": hi_sid,
                "source": "joint_scenario_hull",
                "joint_endpoints": True,
            }
        result[aid] = item
    return result


def threshold_predicate(value: float, th: Dict[str, Any]) -> bool:
    target = th["value"]
    op = th["op"]
    if op == "<=":
        return value <= target + 1e-10
    if op == ">=":
        return value >= target - 1e-10
    if op == "<":
        return value < target - 1e-10
    if op == ">":
        return value > target + 1e-10
    raise DatasetError(f"unsupported operator {op}")


def assess_thresholds(
    dataset: Dict[str, Any], thresholds: Sequence[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    metrics = metric_map(dataset)
    result: Dict[str, Dict[str, Any]] = {}
    scenarios = scenario_list(dataset)
    for alt in dataset.get("alternatives", []):
        aid = alt["id"]
        checks = []
        states = []
        for th in thresholds:
            mid = th["metric_id"]
            if not has_joint_values(aid, dataset)[0]:
                # Marginal intervals cannot prove robust compliance.
                rng = (alt.get("intervals", {}) or {}).get(mid)
                if not rng:
                    state = "unknown"
                else:
                    # For min threshold, worst case is max; for max threshold,
                    # worst case is min. This is a legitimate marginal bound.
                    worst = float(rng[1]) if metrics[mid]["direction"] == "min" else float(rng[0])
                    state = "pass" if threshold_predicate(worst, th) else "fail"
                checks.append({"threshold": th, "state": state, "values": []})
                states.append(state)
                continue

            values = []
            for s in scenarios:
                raw = float(s["values"][aid][mid])
                ok = threshold_predicate(raw, th)
                values.append({"scenario_id": s["id"], "value": raw, "pass": ok})
            passes = [v for v in values if v["pass"]]
            if len(passes) == len(values):
                state = "pass"
            elif not passes:
                state = "fail"
            else:
                state = "risk"
            checks.append(
                {
                    "threshold": th,
                    "state": state,
                    "passing_scenarios": [v["scenario_id"] for v in passes],
                    "failing_scenarios": [v["scenario_id"] for v in values if not v["pass"]],
                    "values": values,
                }
            )
            states.append(state)

        if not thresholds:
            overall = "eligible"
        elif "fail" in states:
            overall = "infeasible"
        elif "risk" in states or "unknown" in states:
            overall = "uncertain"
        else:
            overall = "eligible"
        result[aid] = {"overall": overall, "checks": checks}
    return result


def compare_pair(
    a_id: str, b_id: str, dataset: Dict[str, Any]
) -> Dict[str, Any]:
    metrics = dataset["metrics"]
    scenarios = scenario_list(dataset)
    rows = []
    a_no_worse = True
    b_no_worse = True
    strict_ab = False
    strict_ba = False
    equivalent = True
    strict_example_ab = None
    strict_example_ba = None

    for scenario in scenarios:
        row: Dict[str, Any] = {"scenario_id": scenario["id"], "scenario_name": scenario.get("name", scenario["id"]), "metrics": []}
        for metric in metrics:
            mid = metric["id"]
            va = float(scenario["values"][a_id][mid])
            vb = float(scenario["values"][b_id][mid])
            ua = desirability(va, metric)
            ub = desirability(vb, metric)
            # Compare normalized utility so EPS is independent of unit/scale.
            if ua > ub + EPS:
                cmp = 1
            elif ua < ub - EPS:
                cmp = -1
            else:
                cmp = 0
            if cmp == -1:  # b is better than a => a has a violation vs b
                a_no_worse = False
            if cmp == 1:  # a is better than b
                b_no_worse = False
            if cmp == 1:
                strict_ab = True
                if strict_example_ab is None or ua - ub > strict_example_ab["utility_delta"]:
                    strict_example_ab = {
                        "scenario_id": scenario["id"],
                        "metric_id": mid,
                        "a_value": va,
                        "b_value": vb,
                        "utility_delta": ua - ub,
                    }
            if cmp == -1:
                strict_ba = True
                if strict_example_ba is None or ub - ua > strict_example_ba["utility_delta"]:
                    strict_example_ba = {
                        "scenario_id": scenario["id"],
                        "metric_id": mid,
                        "a_value": va,
                        "b_value": vb,
                        "utility_delta": ub - ua,
                    }
            if abs(ua - ub) > 1e-7:
                equivalent = False
            row["metrics"].append(
                {
                    "metric_id": mid,
                    "a_value": va,
                    "b_value": vb,
                    "a_utility": ua,
                    "b_utility": ub,
                    "comparison": "a_better" if cmp == 1 else "b_better" if cmp == -1 else "equal",
                }
            )
        rows.append(row)

    a_dominates_b = a_no_worse and strict_ab
    b_dominates_a = b_no_worse and strict_ba
    if equivalent:
        relation = "equivalent"
    elif a_dominates_b:
        relation = "a_dominates_b"
    elif b_dominates_a:
        relation = "b_dominates_a"
    else:
        relation = "tradeoff"

    return {
        "a": a_id,
        "b": b_id,
        "relation": relation,
        "a_dominates_b": a_dominates_b,
        "b_dominates_a": b_dominates_a,
        "equivalent": equivalent,
        "checked_scenarios": len(scenarios),
        "strict_example_a_over_b": strict_example_ab,
        "strict_example_b_over_a": strict_example_ba,
        "rows": rows,
        "note": "同一联合场景内逐维比较；未跨场景拼接端点。",
    }


def find_optimal_witness(
    alt_id: str,
    comparator_ids: Sequence[str],
    scenario: Dict[str, Any],
    dataset: Dict[str, Any],
    ranges: Dict[str, Tuple[float, float]],
) -> Optional[Dict[str, Any]]:
    metrics = dataset["metrics"]
    mids = [m["id"] for m in metrics]
    lower = [ranges[mid][0] for mid in mids]
    upper = [ranges[mid][1] for mid in mids]

    utilities: Dict[str, List[float]] = {}
    for aid in [alt_id, *comparator_ids]:
        utilities[aid] = [
            desirability(float(scenario["values"][aid][mid]), metric)
            for mid, metric in zip(mids, metrics)
        ]

    inequalities = []
    ua = utilities[alt_id]
    for cid in comparator_ids:
        diff = [ua[i] - utilities[cid][i] for i in range(len(mids))]
        inequalities.append((diff, 0.0))

    weights, reason = feasible_weight_point(lower, upper, inequalities)
    if weights is None:
        return None

    weight_dict = {mid: weights[i] for i, mid in enumerate(mids)}
    scores = []
    for aid in [alt_id, *comparator_ids]:
        score = sum(weights[i] * utilities[aid][i] for i in range(len(mids)))
        scores.append({"alternative_id": aid, "score": score, "utilities": dict(zip(mids, utilities[aid]))})
    scores.sort(key=lambda x: x["score"], reverse=True)
    top = scores[0]
    if top["alternative_id"] != alt_id and abs(top["score"] - next(s["score"] for s in scores if s["alternative_id"] == alt_id)) > 1e-6:
        # Numerical basis should have prevented this; reject false witness.
        return None
    alt_score = next(s["score"] for s in scores if s["alternative_id"] == alt_id)
    runner_up = scores[1]["score"] if len(scores) > 1 else alt_score
    active = []
    for cid in comparator_ids:
        margin = alt_score - next(s["score"] for s in scores if s["alternative_id"] == cid)
        if abs(margin) < 2e-5:
            active.append(cid)
    return {
        "scenario_id": scenario["id"],
        "scenario_name": scenario.get("name", scenario["id"]),
        "weights": weight_dict,
        "scores": scores,
        "margin_over_runner_up": max(0.0, alt_score - runner_up),
        "active_ties": active,
        "weight_constraint_note": "该权重是给定范围与半空间中的一个存在性见证，不代表系统替你选定唯一权重。",
    }


def _union_equivalent_groups(pairs: Sequence[Dict[str, Any]], alt_ids: Sequence[str]) -> List[List[str]]:
    parent = {x: x for x in alt_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for p in pairs:
        if p["equivalent"]:
            union(p["a"], p["b"])
    groups: Dict[str, List[str]] = {}
    for aid in alt_ids:
        groups.setdefault(find(aid), []).append(aid)
    return [g for g in groups.values() if len(g) > 1]


def threshold_relaxation(dataset: Dict[str, Any], thresholds: Sequence[Dict[str, Any]], threshold_assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
    """For contradictory locks, calculate the smallest joint robust relaxation."""
    if not thresholds:
        return []
    metrics = metric_map(dataset)
    bounds = compute_bounds(dataset)
    suggestions = []
    for alt in dataset.get("alternatives", []):
        aid = alt["id"]
        if bounds[aid].get("evidence_mode") != "joint":
            continue
        required = {}
        normalized_violation = 0.0
        for th in thresholds:
            mid = th["metric_id"]
            b = bounds[aid]["metrics"].get(mid)
            if not b:
                continue
            metric = metrics[mid]
            if metric["direction"] == "min":
                # Need cap at least as large as worst/high value.
                required[mid] = b["max"]
                if th["op"] == "<=":
                    scale_span = max(1e-9, metric["scale"][1] - metric["scale"][0])
                    normalized_violation += max(0.0, b["max"] - th["value"]) / scale_span
            else:
                # Need floor no higher than worst/low value.
                required[mid] = b["min"]
                if th["op"] == ">=":
                    scale_span = max(1e-9, metric["scale"][1] - metric["scale"][0])
                    normalized_violation += max(0.0, th["value"] - b["min"]) / scale_span
        suggestions.append(
            {
                "alternative_id": aid,
                "normalized_violation": normalized_violation,
                "required_threshold_values": required,
                "would_become": "eligible_after_joint_relaxation",
            }
        )
    suggestions.sort(key=lambda x: x["normalized_violation"])
    return suggestions


def analyze_dataset(dataset: Dict[str, Any], options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(dataset, dict) or not dataset.get("metrics") or not dataset.get("alternatives"):
        raise DatasetError("数据必须包含 metrics 和 alternatives")
    opts = normalize_options(dataset, options)
    metrics = dataset["metrics"]
    alt_ids = [a["id"] for a in dataset["alternatives"]]
    scenarios = scenario_list(dataset)
    ranges = opts["weight_ranges"]
    thresholds = opts["thresholds"]

    lower = [ranges[m["id"]][0] for m in metrics]
    upper = [ranges[m["id"]][1] for m in metrics]
    base_weights, weight_error = feasible_weight_point(lower, upper)
    weight_state = "feasible" if base_weights is not None else "infeasible"

    bounds = compute_bounds(dataset)
    threshold_assessment = assess_thresholds(dataset, thresholds)

    complete: Dict[str, bool] = {}
    missing: Dict[str, List[str]] = {}
    for aid in alt_ids:
        complete[aid], missing[aid] = has_joint_values(aid, dataset)

    pairs: List[Dict[str, Any]] = []
    complete_ids = [aid for aid in alt_ids if complete[aid]]
    for i, a in enumerate(complete_ids):
        for b in complete_ids[i + 1 :]:
            pairs.append(compare_pair(a, b, dataset))

    dominated_by: Dict[str, List[str]] = {aid: [] for aid in alt_ids}
    for p in pairs:
        if p["a_dominates_b"]:
            dominated_by[p["b"]].append(p["a"])
        if p["b_dominates_a"]:
            dominated_by[p["a"]].append(p["b"])

    equivalent_groups = _union_equivalent_groups(pairs, complete_ids)
    equivalent_of = {}
    for group in equivalent_groups:
        for aid in group:
            equivalent_of[aid] = [x for x in group if x != aid]

    eligible_ids = [
        aid for aid in complete_ids if threshold_assessment[aid]["overall"] == "eligible"
    ]

    alternatives_result = []
    possible_winner_ids: List[str] = []
    for alt in dataset["alternatives"]:
        aid = alt["id"]
        is_complete = complete[aid]
        doms = dominated_by.get(aid, [])
        th_state = threshold_assessment[aid]["overall"]

        witness = None
        witness_error = None
        if weight_state != "feasible":
            witness_error = weight_error
        elif aid in eligible_ids:
            comparators = [x for x in eligible_ids if x != aid]
            # Prefer a scenario in which the candidate has its best raw mean?
            # Existence is checked in every scenario anyway; order stable.
            for scenario in scenarios:
                witness = find_optimal_witness(aid, comparators, scenario, dataset, ranges)
                if witness is not None:
                    break
            if witness is None:
                witness_error = "no_weight_in_range_makes_alternative_best"

        conditional_witness = None
        conditional_scenarios = []
        if thresholds and is_complete and weight_state == "feasible":
            for scenario in scenarios:
                if all(threshold_predicate(float(scenario["values"][aid][th["metric_id"]]), th) for th in thresholds):
                    conditional_scenarios.append(scenario["id"])
            # Conditional set in the first scenario where alt passes all locks.
            for scenario in scenarios:
                alt_passes = all(
                    threshold_predicate(float(scenario["values"][aid][th["metric_id"]]), th)
                    for th in thresholds
                )
                if not alt_passes:
                    continue
                conditional_peers = []
                for cid in complete_ids:
                    if cid == aid:
                        continue
                    if all(
                        threshold_predicate(float(scenario["values"][cid][th["metric_id"]]), th)
                        for th in thresholds
                    ):
                        conditional_peers.append(cid)
                conditional_witness = find_optimal_witness(aid, conditional_peers, scenario, dataset, ranges)
                if conditional_witness:
                    break

        if not is_complete:
            decision_class = "insufficient_evidence"
            reason_code = "missing_joint_evidence"
            reason = "缺少完整联合场景或明确约束；不能把各指标区间端点自行拼成一个联合状态。"
        elif doms:
            decision_class = "dominated"
            reason_code = "robust_dominance"
            reason = f"在全部 {len(scenarios)} 个联合场景中均不优于支配方案，且至少一维明确更差。"
        elif th_state == "infeasible":
            decision_class = "threshold_blocked"
            reason_code = "hard_threshold_failed_in_every_scenario"
            reason = "锁定阈值在所有联合场景中均不满足，属于硬阈值排除。"
        elif th_state == "uncertain":
            decision_class = "insufficient_evidence"
            reason_code = "hard_threshold_not_robustly_assured"
            reason = "部分联合场景违反不可退让阈值；在补测确认前不能稳健入围。"
        elif weight_state != "feasible":
            decision_class = "preference_excluded"
            reason_code = weight_error
            reason = "权重范围与归一化权重之和为 1 不相容，需要调整偏好范围。"
        elif witness is not None:
            decision_class = "possible_effective"
            reason_code = "optimal_witness_exists"
            reason = "存在一组偏好范围内的权重和一个联合场景，使该方案得分最高。"
            possible_winner_ids.append(aid)
        else:
            decision_class = "preference_excluded"
            reason_code = witness_error or "no_optimal_witness"
            reason = "当前权重范围内不存在使其成为最优的权重；这是偏好范围排除，不一定是数据矛盾。"

        alternatives_result.append(
            {
                "id": aid,
                "name": alt.get("name", aid),
                "process": alt.get("process", ""),
                "vendor": alt.get("vendor", ""),
                "notes": alt.get("notes", ""),
                "tags": alt.get("tags", []),
                "decision_class": decision_class,
                "class_label": CLASS_LABELS[decision_class],
                "reason_code": reason_code,
                "reason": reason,
                "evidence_complete": is_complete,
                "missing_evidence": missing.get(aid, []),
                "dominated_by": doms,
                "equivalent_to": equivalent_of.get(aid, []),
                "threshold_state": th_state,
                "shortlisted": th_state == "eligible" and is_complete,
                "optimal_witness": witness,
                "conditional_optimal_witness": conditional_witness,
                "conditional_pass_scenarios": conditional_scenarios,
                "bounds": bounds[aid],
            }
        )

    tradeoff_pairs = [p for p in pairs if p["relation"] == "tradeoff"]
    # Decision-relevant pair ambiguity only concerns schemes that can still be
    # optimal under the locks/weight band.  Pairs involving an already robustly
    # dominated alternative do not keep the final choice open.
    winner_set = set(possible_winner_ids)
    relevant_tradeoff_pairs = [
        p for p in tradeoff_pairs if p["a"] in winner_set and p["b"] in winner_set
    ]
    insufficient_count = sum(1 for a in alternatives_result if a["decision_class"] == "insufficient_evidence")
    dominated_count = sum(1 for a in alternatives_result if a["decision_class"] == "dominated")
    possible_count = sum(1 for a in alternatives_result if a["decision_class"] == "possible_effective")
    blocked_count = sum(1 for a in alternatives_result if a["decision_class"] == "threshold_blocked")
    preference_excluded_count = sum(1 for a in alternatives_result if a["decision_class"] == "preference_excluded")

    contradiction = None
    if thresholds and not eligible_ids:
        any_conditional_point = any(
            a["conditional_pass_scenarios"] for a in alternatives_result
        )
        relaxation = threshold_relaxation(dataset, thresholds, threshold_assessment)
        contradiction = {
            "type": "no_joint_point_passes_all_locks" if not any_conditional_point else "no_robust_compliance",
            "title": "锁定阈值相互冲突或尚不能稳健满足",
            "detail": (
                "枚举全部联合场景后，没有任何方案在同一场景同时通过所有锁定阈值。"
                if not any_conditional_point
                else "存在场景化达标点，但没有方案在全部场景中稳健达标；不可退让阈值下仍不能入围。"
            ),
            "relaxation_candidates": relaxation,
        }

    ambiguity = {
        "possible_winner_count": len(possible_winner_ids),
        "choice_ambiguity": max(0, len(possible_winner_ids) - 1),
        "unresolved_tradeoff_pairs": len(relevant_tradeoff_pairs),
        "evidence_gaps": insufficient_count,
        "score": max(0, len(possible_winner_ids) - 1)
        + len(relevant_tradeoff_pairs)
        + insufficient_count,
        "formula": "歧义指数 = max(0, 可成为最优的方案数-1) + 可能最优方案间未解决权衡对数 + 联合证据缺口数",
    }

    return {
        "dataset_name": dataset.get("name", "未命名数据"),
        "metrics": metrics,
        "weight_ranges": {mid: list(ranges[mid]) for mid in [m["id"] for m in metrics]},
        "weight_state": weight_state,
        "weight_error": weight_error,
        "thresholds": thresholds,
        "scenario_count": len(scenarios),
        "scenarios": [{"id": s["id"], "name": s.get("name", s["id"])} for s in scenarios],
        "alternatives": alternatives_result,
        "pairs": pairs,
        "equivalent_groups": equivalent_groups,
        "possible_winner_ids": possible_winner_ids,
        "eligible_ids": eligible_ids,
        "threshold_assessment": threshold_assessment,
        "threshold_contradiction": contradiction,
        "ambiguity": ambiguity,
        "counts": {
            "total": len(alt_ids),
            "dominated": dominated_count,
            "possible_effective": possible_count,
            "insufficient_evidence": insufficient_count,
            "threshold_blocked": blocked_count,
            "preference_excluded": preference_excluded_count,
            "tradeoff_pairs": len(tradeoff_pairs),
        },
        "principle": "支配与最优见证均在同一联合场景上计算；区间端点只作为联合场景包络展示，不做跨维笛卡尔拼接。",
    }
