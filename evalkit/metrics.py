from __future__ import annotations

import sqlite3
from collections import defaultdict
from itertools import combinations

from evalkit.golden import GoldenLabel, golden_label_map


def calculate_metrics(case_rows: list[sqlite3.Row], dimension_rows: list[sqlite3.Row], human_rows: list[sqlite3.Row]) -> dict:
    total_cases = len(case_rows)
    passed_cases = sum(1 for row in case_rows if row["passed"] == 1)
    evaluated_cases = sum(1 for row in case_rows if row["passed"] is not None)
    pass_rate = passed_cases / evaluated_cases if evaluated_cases else None

    by_dimension: dict[str, dict] = {}
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in dimension_rows:
        groups[row["dimension_name"]].append(row)
    for name, rows in groups.items():
        evaluated = [row for row in rows if row["passed"] is not None]
        by_dimension[name] = {
            "total": len(rows),
            "evaluated": len(evaluated),
            "pass_rate": sum(1 for row in evaluated if row["passed"] == 1) / len(evaluated) if evaluated else None,
            "needs_human_review": sum(1 for row in rows if row["requires_human_review"] == 1),
        }

    agreement = _human_machine_agreement(dimension_rows, human_rows)
    return {
        "total_cases": total_cases,
        "pass_rate": pass_rate,
        "by_dimension": by_dimension,
        "human_machine_agreement": agreement,
    }


def _human_machine_agreement(dimension_rows: list[sqlite3.Row], human_rows: list[sqlite3.Row]) -> float | None:
    machine = {(row["case_id"], row["dimension_name"]): row["passed"] for row in dimension_rows if row["passed"] is not None}
    comparable = []
    for row in human_rows:
        key = (row["case_id"], row["dimension_name"])
        if key in machine:
            comparable.append(machine[key] == row["passed"])
    if not comparable:
        return None
    return sum(1 for item in comparable if item) / len(comparable)


def calculate_reliability_metrics(dimension_rows: list[sqlite3.Row], golden_labels: list[GoldenLabel]) -> dict:
    labels = golden_label_map(golden_labels)
    pairs = []
    for row in dimension_rows:
        key = (row["case_id"], row["dimension_name"])
        if key in labels and row["passed"] is not None:
            pairs.append((bool(row["passed"]), labels[key].expected_passed, row["dimension_name"]))

    overall = _classification_metrics([(predicted, expected) for predicted, expected, _ in pairs])
    by_dimension: dict[str, dict] = {}
    grouped: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for predicted, expected, dimension_name in pairs:
        grouped[dimension_name].append((predicted, expected))
    for dimension_name, values in grouped.items():
        by_dimension[dimension_name] = _classification_metrics(values)

    return {
        "matched_labels": len(pairs),
        "total_golden_labels": len(golden_labels),
        "overall": overall,
        "by_dimension": by_dimension,
    }


def calculate_calibration_metrics(
    dimension_rows: list[sqlite3.Row],
    human_rows: list[sqlite3.Row],
    golden_labels: list[GoldenLabel],
) -> dict:
    labels = golden_label_map(golden_labels)
    machine = {
        (row["case_id"], row["dimension_name"]): bool(row["passed"])
        for row in dimension_rows
        if row["passed"] is not None
    }
    human_by_key: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in human_rows:
        human_by_key[(row["case_id"], row["dimension_name"])].append(row)

    human_machine = []
    human_golden = []
    reviewer_pairs: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    human_human = []
    for key, rows in human_by_key.items():
        for row in rows:
            human_passed = bool(row["passed"])
            if key in machine:
                human_machine.append(machine[key] == human_passed)
            if key in labels:
                human_golden.append((human_passed, labels[key].expected_passed))
                reviewer_pairs[row["reviewer"]].append((human_passed, labels[key].expected_passed))
        for left, right in combinations(rows, 2):
            human_human.append(bool(left["passed"]) == bool(right["passed"]))

    return {
        "human_machine_agreement": _agreement(human_machine),
        "human_human_pairwise_agreement": _agreement(human_human),
        "human_vs_golden": _classification_metrics(human_golden),
        "reviewer_vs_golden": {
            reviewer: _classification_metrics(values)
            for reviewer, values in reviewer_pairs.items()
        },
    }


def calculate_outcome_correlations(
    case_rows: list[sqlite3.Row],
    dimension_rows: list[sqlite3.Row],
    outcomes: dict[str, dict[str, float]],
) -> dict:
    case_pass = {
        row["case_id"]: row["passed"]
        for row in case_rows
        if row["passed"] is not None and row["case_id"] in outcomes
    }
    dimension_pass: dict[str, dict[str, int]] = defaultdict(dict)
    for row in dimension_rows:
        if row["passed"] is not None and row["case_id"] in outcomes:
            dimension_pass[row["dimension_name"]][row["case_id"]] = int(row["passed"])

    metric_names = sorted({metric for values in outcomes.values() for metric in values})
    overall = {
        metric: _correlation_for_cases(case_pass, outcomes, metric)
        for metric in metric_names
    }
    by_dimension = {
        dimension_name: {
            metric: _correlation_for_cases(values, outcomes, metric)
            for metric in metric_names
        }
        for dimension_name, values in dimension_pass.items()
    }
    return {"overall_pass": overall, "by_dimension": by_dimension}


def _classification_metrics(pairs: list[tuple[bool, bool]]) -> dict:
    tp = sum(1 for predicted, expected in pairs if predicted and expected)
    tn = sum(1 for predicted, expected in pairs if not predicted and not expected)
    fp = sum(1 for predicted, expected in pairs if predicted and not expected)
    fn = sum(1 for predicted, expected in pairs if not predicted and expected)
    total = len(pairs)
    return {
        "total": total,
        "accuracy": (tp + tn) / total if total else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "recall": tp / (tp + fn) if (tp + fn) else None,
        "false_positive_rate": fp / (fp + tn) if (fp + tn) else None,
        "false_negative_rate": fn / (fn + tp) if (fn + tp) else None,
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
    }


def _agreement(values: list[bool]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value) / len(values)


def _correlation_for_cases(case_values: dict[str, int], outcomes: dict[str, dict[str, float]], metric_name: str) -> dict:
    pairs = [
        (float(value), outcomes[case_id][metric_name])
        for case_id, value in case_values.items()
        if metric_name in outcomes[case_id]
    ]
    return {
        "n": len(pairs),
        "pearson": _pearson(pairs),
    }


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator_x = sum((x - mean_x) ** 2 for x in xs)
    denominator_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = (denominator_x * denominator_y) ** 0.5
    if denominator == 0:
        return None
    return numerator / denominator
