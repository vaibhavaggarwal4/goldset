from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from evalkit.errors import UserFacingError


@dataclass(frozen=True)
class GoldenLabel:
    case_id: str
    dimension_name: str
    expected_passed: bool
    expected_score: float | None = None
    labeler: str = ""
    notes: str = ""


def load_golden_set(path: str | Path) -> list[GoldenLabel]:
    source = Path(path)
    if not source.exists():
        raise UserFacingError(
            f"Golden set CSV not found: {source}\n"
            "Fix: pass a valid --golden-set path."
        )
    labels: list[GoldenLabel] = []
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"case_id", "dimension_name", "expected_passed"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise UserFacingError(
                f"Golden set CSV must include columns: {', '.join(sorted(required))}.\n"
                "Optional columns: expected_score, labeler, notes."
            )
        for index, row in enumerate(reader, start=2):
            expected = _parse_bool(row.get("expected_passed", ""), row_number=index)
            score = _parse_float(row.get("expected_score", ""))
            labels.append(
                GoldenLabel(
                    case_id=row["case_id"],
                    dimension_name=row["dimension_name"],
                    expected_passed=expected,
                    expected_score=score,
                    labeler=row.get("labeler", ""),
                    notes=row.get("notes", ""),
                )
            )
    if not labels:
        raise UserFacingError(
            f"Golden set CSV has no labels: {source}\n"
            "Fix: add one row per case_id + dimension_name label."
        )
    return labels


def load_outcomes(path: str | Path) -> dict[str, dict[str, float]]:
    source = Path(path)
    if not source.exists():
        raise UserFacingError(
            f"Outcomes CSV not found: {source}\n"
            "Fix: pass a valid --outcomes path."
        )
    outcomes: dict[str, dict[str, float]] = {}
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "case_id" not in reader.fieldnames:
            raise UserFacingError(
                "Outcomes CSV must include a case_id column and at least one numeric metric column."
            )
        metric_columns = [column for column in reader.fieldnames if column != "case_id"]
        for row in reader:
            case_id = row.get("case_id", "")
            if not case_id:
                continue
            metrics: dict[str, float] = {}
            for column in metric_columns:
                value = _parse_float(row.get(column, ""))
                if value is not None:
                    metrics[column] = value
            if metrics:
                outcomes[case_id] = metrics
    if not outcomes:
        raise UserFacingError(
            f"Outcomes CSV has no numeric metrics: {source}\n"
            "Fix: add columns such as ctr, conversion_rate, reply_rate, activation_rate, or revenue."
        )
    return outcomes


def golden_label_map(labels: list[GoldenLabel]) -> dict[tuple[str, str], GoldenLabel]:
    return {(label.case_id, label.dimension_name): label for label in labels}


def _parse_bool(value: str, row_number: int) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "t", "yes", "y", "1", "pass", "passed"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "fail", "failed"}:
        return False
    raise UserFacingError(
        f"Invalid expected_passed value on golden set row {row_number}: {value!r}\n"
        "Fix: use true/false, pass/fail, yes/no, or 1/0."
    )


def _parse_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
