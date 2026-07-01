from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evalkit.errors import UserFacingError


CANONICAL_DATA_FIELDS = [
    "case_id",
    "artifact_type",
    "channel",
    "audience",
    "campaign_goal",
    "stage",
    "input",
    "subject_line",
    "body",
    "headline",
    "primary_text",
    "output",
]


@dataclass(frozen=True)
class CsvInspection:
    path: Path
    row_count: int
    columns: list[str]
    likely_id_columns: list[str]
    likely_content_columns: list[str]
    likely_outcome_columns: list[str]


def inspect_csv(path: str | Path) -> CsvInspection:
    source = _validate_csv(path)
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        row_count = sum(1 for _ in reader)
    if not columns:
        raise UserFacingError(
            f"CSV has no header row: {source}\n"
            "Fix: export a CSV with column names in the first row."
        )
    return CsvInspection(
        path=source,
        row_count=row_count,
        columns=columns,
        likely_id_columns=_likely_columns(columns, ["id", "campaign", "message", "email", "creative"]),
        likely_content_columns=_likely_columns(columns, ["subject", "body", "copy", "content", "headline", "text", "description"]),
        likely_outcome_columns=_likely_columns(columns, ["click", "ctr", "conversion", "reply", "open", "revenue", "pipeline", "activation"]),
    )


def import_data(source: str | Path, mapping_path: str | Path, output: str | Path) -> Path:
    source_path = _validate_csv(source)
    mapping = _load_mapping(mapping_path)
    output_path = Path(output)
    rows = _read_rows(source_path)
    transformed = [_transform_row(row, mapping) for row in rows]
    _validate_required_fields(transformed, ["case_id", "output"])
    _write_rows(output_path, transformed, CANONICAL_DATA_FIELDS)
    return output_path


def import_outcomes(source: str | Path, mapping_path: str | Path, output: str | Path) -> Path:
    source_path = _validate_csv(source)
    mapping = _load_mapping(mapping_path)
    output_path = Path(output)
    rows = _read_rows(source_path)
    transformed = [_transform_row(row, mapping) for row in rows]
    _validate_required_fields(transformed, ["case_id"])
    fieldnames = _ordered_fields(transformed, preferred_first=["case_id"])
    _write_rows(output_path, transformed, fieldnames)
    return output_path


def _validate_csv(path: str | Path) -> Path:
    source = Path(path)
    if not source.exists():
        raise UserFacingError(f"CSV not found: {source}")
    if source.suffix.lower() != ".csv":
        raise UserFacingError(f"Expected a .csv file, got: {source}")
    return source


def _load_mapping(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise UserFacingError(f"Mapping YAML not found: {source}")
    try:
        data = yaml.safe_load(source.read_text()) or {}
    except yaml.YAMLError as exc:
        raise UserFacingError(
            f"Mapping YAML could not be parsed: {source}\n"
            f"Parser detail: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise UserFacingError("Mapping YAML must be an object.")
    fields = data.get("fields", data)
    if not isinstance(fields, dict):
        raise UserFacingError("Mapping YAML must contain field mappings.")
    return fields


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise UserFacingError(f"CSV has no header row: {path}")
        rows = list(reader)
    if not rows:
        raise UserFacingError(f"CSV has no data rows: {path}")
    return rows


def _transform_row(row: dict[str, str], mapping: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for target_field, spec in mapping.items():
        output[target_field] = _resolve_spec(row, spec)
    return output


def _resolve_spec(row: dict[str, str], spec: Any) -> str:
    if isinstance(spec, str):
        return row.get(spec, "")
    if isinstance(spec, (int, float, bool)):
        return str(spec)
    if isinstance(spec, dict):
        if "constant" in spec:
            return str(spec["constant"])
        if "column" in spec:
            return row.get(str(spec["column"]), "")
        if "join" in spec:
            columns = spec["join"] or []
            separator = str(spec.get("separator", " "))
            values = [row.get(str(column), "").strip() for column in columns]
            return separator.join(value for value in values if value)
        if "coalesce" in spec:
            for column in spec["coalesce"] or []:
                value = row.get(str(column), "").strip()
                if value:
                    return value
            return ""
    raise UserFacingError(f"Unsupported mapping spec: {spec!r}")


def _validate_required_fields(rows: list[dict[str, str]], required: list[str]) -> None:
    for field in required:
        if not any(row.get(field) for row in rows):
            raise UserFacingError(
                f"Imported CSV is missing required field '{field}'.\n"
                "Fix: update the mapping YAML so this field maps to a populated source column."
            )


def _ordered_fields(rows: list[dict[str, str]], preferred_first: list[str]) -> list[str]:
    fields = []
    for field in preferred_first:
        if any(field in row for row in rows):
            fields.append(field)
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    return fields


def _write_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extras = sorted({field for row in rows for field in row if field not in fieldnames})
    all_fields = fieldnames + extras
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=all_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in all_fields})


def _likely_columns(columns: list[str], keywords: list[str]) -> list[str]:
    matches = []
    for column in columns:
        normalized = column.lower().replace("_", " ")
        if any(keyword in normalized for keyword in keywords):
            matches.append(column)
    return matches[:12]
