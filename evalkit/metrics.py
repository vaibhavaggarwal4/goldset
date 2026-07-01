from __future__ import annotations

import sqlite3
from collections import defaultdict


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
