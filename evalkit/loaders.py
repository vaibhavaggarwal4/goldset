from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from evalkit.errors import UserFacingError
from evalkit.models import EvalArtifact, EvalCase


def load_cases_from_csv(path: str | Path, artifact_type: str = "text") -> list[EvalCase]:
    """Load CSV rows exported from a spreadsheet or saved locally."""

    source = Path(path)
    if not source.exists():
        raise UserFacingError(
            f"Input CSV not found: {source}\n"
            "Fix: check the path passed to --input, or export your Google Sheet as a CSV first."
        )
    if source.suffix.lower() != ".csv":
        raise UserFacingError(
            f"Unsupported input file type: {source.suffix or '(none)'}\n"
            "Fix: v0.1 supports CSV files, including Google Sheets exports."
        )

    cases: list[EvalCase] = []
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise UserFacingError(
                f"Input CSV has no header row: {source}\n"
                "Fix: add columns such as case_id, input, output, subject_line, and body."
            )
        for index, row in enumerate(reader, start=1):
            case_id = row.get("case_id") or row.get("id") or f"case-{index}"
            input_text = row.get("input") or row.get("brief") or row.get("prompt") or ""
            output_text = row.get("output") or row.get("content") or row.get("copy") or ""
            metadata = _extract_metadata(row)
            fields = {key: value for key, value in row.items() if value is not None}
            cases.append(
                EvalCase(
                    case_id=case_id,
                    input_text=input_text,
                    artifact=EvalArtifact(
                        content=output_text,
                        artifact_type=row.get("artifact_type") or artifact_type,
                        fields=fields,
                    ),
                    metadata=metadata,
                )
            )
    if not cases:
        raise UserFacingError(
            f"Input CSV has no data rows: {source}\n"
            "Fix: add at least one row to evaluate."
        )
    return cases


def _extract_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    raw_metadata = row.get("metadata_json")
    if raw_metadata:
        try:
            metadata.update(json.loads(raw_metadata))
        except json.JSONDecodeError:
            metadata["metadata_json_parse_error"] = raw_metadata
    for key in ("audience", "campaign_goal", "channel", "product", "stage"):
        if row.get(key):
            metadata[key] = row[key]
    return metadata
