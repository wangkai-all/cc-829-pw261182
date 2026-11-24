"""Synthetic measurement patching and value-of-ambiguity recommendations."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from .analysis import DatasetError, analyze_dataset


def apply_operations(dataset: Dict[str, Any], operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    data = copy.deepcopy(dataset)
    for op in operations or []:
        kind = op.get("op")
        if kind == "retain_scenarios":
            ids = set(op.get("ids", []))
            data["scenarios"] = [s for s in data.get("scenarios", []) if s.get("id") in ids]
        elif kind == "filter_coordinate":
            aid = op["alternative_id"]
            mid = op["metric_id"]
            center = float(op["value"])
            tol = float(op.get("tolerance", 0.0))
            kept = []
            for s in data.get("scenarios", []):
                raw = s.get("values", {}).get(aid, {}).get(mid)
                if raw is not None and abs(float(raw) - center) <= tol + 1e-12:
                    kept.append(s)
            data["scenarios"] = kept
        elif kind == "update_alternative":
            aid = op["id"]
            patch = op.get("patch", {})
            for alt in data.get("alternatives", []):
                if alt.get("id") == aid:
                    alt.update(copy.deepcopy(patch))
                    break
        elif kind == "set_values":
            sid = op["scenario_id"]
            aid = op["alternative_id"]
            values = op.get("values", {})
            for s in data.get("scenarios", []):
                if s.get("id") == sid:
                    s.setdefault("values", {}).setdefault(aid, {}).update(copy.deepcopy(values))
                    break
        elif kind == "replace_intervals":
            aid = op["alternative_id"]
            for alt in data.get("alternatives", []):
                if alt.get("id") == aid:
                    alt.setdefault("intervals", {}).update(copy.deepcopy(op.get("intervals", {})))
                    break
        else:
            raise ValueError(f"unknown patch operation: {kind}")
    return data


def apply_measurement(
    dataset: Dict[str, Any],
    payload: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before = analyze_dataset(dataset, options)
    measurement = None
    outcome = None
    record: Dict[str, Any]

    if payload.get("custom"):
        custom = payload["custom"]
        aid = custom["alternative_id"]
        mid = custom["metric_id"]
        value = float(custom["value"])
        tol = float(custom.get("tolerance", 0.0))
        cost = float(custom.get("cost", 0.0))
        operations: List[Dict[str, Any]] = []
        alt = next((a for a in dataset.get("alternatives", []) if a["id"] == aid), None)
        joint = alt and alt.get("evidence_mode") != "marginal_only"
        if joint:
            operations.append(
                {"op": "filter_coordinate", "alternative_id": aid, "metric_id": mid, "value": value, "tolerance": tol}
            )
        else:
            lo, hi = value - tol, value + tol
            operations.append(
                {"op": "replace_intervals", "alternative_id": aid, "intervals": {mid: [lo, hi]}}
            )
        record = {
            "id": f"custom-{len(dataset.get('applied_measurements', [])) + 1}",
            "title": custom.get("title", f"合成测量：{aid}/{mid}={value:g}"),
            "cost": cost,
            "custom": True,
            "target": {"alternative_id": aid, "metric_id": mid},
            "result": {"value": value, "tolerance": tol},
            "operations": operations,
            "limitation": None if joint else "该方案只有边际区间；单点缩窄区间不会建立指标间联合关系，因此通常不能消除分类歧义。",
        }
    else:
        mid = payload.get("measurement_id")
        oid = payload.get("outcome_id")
        measurement = next((m for m in dataset.get("measurements", []) if m["id"] == mid), None)
        if measurement is None:
            raise ValueError(f"unknown measurement {mid}")
        outcome = next((o for o in measurement.get("outcomes", []) if o["id"] == oid), None)
        if outcome is None:
            raise ValueError(f"unknown outcome {oid}")
        operations = outcome.get("operations", [])
        record = {
            "id": f"{mid}:{oid}",
            "measurement_id": mid,
            "outcome_id": oid,
            "title": measurement.get("name", mid),
            "outcome_label": outcome.get("label", oid),
            "cost": float(measurement.get("cost", 0.0)),
            "custom": False,
            "operations": operations,
        }

    patched = apply_operations(dataset, operations)
    after = analyze_dataset(patched, options)
    if before["scenario_count"] > 0 and after["scenario_count"] == 0:
        raise DatasetError(
            "补测结果排除了全部联合场景；观测值或可信半宽与现有场景不相容，未应用该结果。请放宽半宽或导入新的联合场景。"
        )
    diff = diff_analyses(before, after, record["cost"])
    record["explanation"] = diff["explanation"]
    return {"dataset": patched, "applied_record": record, "before": before, "after": after, "diff": diff}


def _class_map(analysis: Dict[str, Any]) -> Dict[str, str]:
    return {a["id"]: a["decision_class"] for a in analysis["alternatives"]}


def diff_analyses(before: Dict[str, Any], after: Dict[str, Any], cost: float = 0.0) -> Dict[str, Any]:
    b_winners = set(before["possible_winner_ids"])
    a_winners = set(after["possible_winner_ids"])
    removed_winners = sorted(b_winners - a_winners)
    added_winners = sorted(a_winners - b_winners)
    b_classes = _class_map(before)
    a_classes = _class_map(after)
    class_changes = []
    for aid in sorted(set(b_classes) | set(a_classes)):
        if b_classes.get(aid) != a_classes.get(aid):
            class_changes.append(
                {"alternative_id": aid, "before": b_classes.get(aid), "after": a_classes.get(aid)}
            )

    b_amb = before["ambiguity"]["score"]
    a_amb = after["ambiguity"]["score"]
    reduction = max(0, b_amb - a_amb)
    spent = float(cost or 0.0)
    explanation_parts = [
        f"联合场景 {before['scenario_count']} → {after['scenario_count']} 个",
        f"可能最优集合 {sorted(b_winners) or '∅'} → {sorted(a_winners) or '∅'}",
        f"歧义指数 {b_amb:g} → {a_amb:g}（排除 {reduction:g}）",
        f"费用 {spent:g}",
    ]
    if removed_winners:
        explanation_parts.append(f"排除候选：{', '.join(removed_winners)}")
    if class_changes:
        labels = before.get("alternatives", [])
        name_map = {x["id"]: x["name"] for x in labels}
        changes = [
            f"{name_map.get(c['alternative_id'], c['alternative_id'])}：{c['before']}→{c['after']}"
            for c in class_changes
        ]
        explanation_parts.append("状态变化：" + "；".join(changes))
    explanation = "。".join(explanation_parts) + "。"
    return {
        "baseline_ambiguity": b_amb,
        "after_ambiguity": a_amb,
        "ambiguity_reduction": reduction,
        "removed_winner_ids": removed_winners,
        "added_winner_ids": added_winners,
        "class_changes": class_changes,
        "cost": spent,
        "explanation": explanation,
    }


def recommend_measurements(
    dataset: Dict[str, Any], options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    baseline = analyze_dataset(dataset, options)
    rows = []
    for measurement in dataset.get("measurements", []):
        outcome_rows = []
        expected_after = 0.0
        probability_total = 0.0
        reductions = []
        removed_sets = []
        for outcome in measurement.get("outcomes", []):
            patched = apply_operations(dataset, outcome.get("operations", []))
            after = analyze_dataset(patched, options)
            diff = diff_analyses(baseline, after, measurement.get("cost", 0.0))
            prob = float(outcome.get("probability", 1.0 / max(1, len(measurement.get("outcomes", [])))))
            probability_total += prob
            expected_after += prob * after["ambiguity"]["score"]
            reductions.append(diff["ambiguity_reduction"])
            removed_sets.append(set(diff["removed_winner_ids"]))
            outcome_rows.append(
                {
                    "outcome_id": outcome["id"],
                    "label": outcome.get("label", outcome["id"]),
                    "probability": prob,
                    "retain_summary": outcome.get("summary", ""),
                    "after_ambiguity": after["ambiguity"],
                    "diff": diff,
                }
            )
        if outcome_rows and probability_total:
            expected_after /= probability_total
        baseline_score = baseline["ambiguity"]["score"]
        expected_reduction = max(0.0, baseline_score - expected_after)
        cost = float(measurement.get("cost", 0.0))
        common_removed = set.intersection(*removed_sets) if removed_sets else set()
        row = {
            "measurement_id": measurement["id"],
            "name": measurement.get("name", measurement["id"]),
            "target": measurement.get("target", {}),
            "description": measurement.get("description", ""),
            "cost": cost,
            "baseline_ambiguity": baseline_score,
            "expected_after_ambiguity": expected_after,
            "expected_reduction": expected_reduction,
            "guaranteed_reduction": min(reductions) if reductions else 0.0,
            "upside_reduction": max(reductions) if reductions else 0.0,
            "guaranteed_removed_winner_ids": sorted(common_removed),
            "roi": expected_reduction / cost if cost > 0 else None,
            "outcomes": outcome_rows,
        }
        rows.append(row)

    # Mark Pareto-efficient cost/reduction choices.
    for row in rows:
        dominated = False
        for other in rows:
            if other["measurement_id"] == row["measurement_id"]:
                continue
            no_worse_cost = other["cost"] <= row["cost"] + 1e-9
            no_worse_gain = other["expected_reduction"] >= row["expected_reduction"] - 1e-9
            strictly_better = (
                other["cost"] < row["cost"] - 1e-9
                or other["expected_reduction"] > row["expected_reduction"] + 1e-9
            )
            if no_worse_cost and no_worse_gain and strictly_better:
                dominated = True
                break
        row["pareto_efficient"] = not dominated

    rows.sort(key=lambda r: (-(r["roi"] if r["roi"] is not None else r["expected_reduction"] * 1000), r["cost"]))
    return {"baseline": baseline["ambiguity"], "recommendations": rows}
