from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from evalkit.errors import UserFacingError
from evalkit.models import Rubric, RubricDimension


def load_rubric(path: str | Path) -> Rubric:
    source = Path(path)
    if not source.exists():
        raise UserFacingError(
            f"Rubric file not found: {source}\n"
            "Fix: pass a valid --rubric path, for example examples/lifecycle_email/rubric.yaml."
        )
    try:
        data = yaml.safe_load(source.read_text()) or {}
    except yaml.YAMLError as exc:
        raise UserFacingError(
            f"Rubric YAML could not be parsed: {source}\n"
            f"Fix: check indentation and YAML syntax. Parser detail: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise UserFacingError(
            f"Rubric must be a YAML object: {source}\n"
            "Fix: start the file with keys like name, version, artifact_type, and dimensions."
        )
    for required_key in ("name", "dimensions"):
        if required_key not in data:
            raise UserFacingError(
                f"Rubric is missing required key '{required_key}': {source}\n"
                "Fix: compare your rubric with examples/lifecycle_email/rubric.yaml."
            )
    dimensions = [_dimension_from_dict(item) for item in data.get("dimensions", [])]
    if not dimensions:
        raise UserFacingError(
            f"Rubric has no dimensions: {source}\n"
            "Fix: add at least one dimension with name, evaluator, and description."
        )
    return Rubric(
        name=data["name"],
        version=str(data.get("version", "0.1")),
        description=data.get("description", ""),
        artifact_type=data.get("artifact_type", "text"),
        dimensions=dimensions,
    )


def _dimension_from_dict(data: dict[str, Any]) -> RubricDimension:
    if not isinstance(data, dict):
        raise UserFacingError("Each rubric dimension must be a YAML object.")
    for required_key in ("name", "evaluator"):
        if required_key not in data:
            raise UserFacingError(
                f"Rubric dimension is missing required key '{required_key}'.\n"
                "Fix: each dimension needs at least name and evaluator."
            )
    if data["evaluator"] not in {"logic", "llm_judge", "human_review"}:
        raise UserFacingError(
            f"Unsupported evaluator '{data['evaluator']}' in dimension '{data['name']}'.\n"
            "Fix: use one of logic, llm_judge, or human_review."
        )
    known = {
        "name",
        "evaluator",
        "description",
        "scale",
        "field",
        "rule",
        "threshold",
        "required",
        "requires_human_review",
        "prompt",
    }
    options = {key: value for key, value in data.items() if key not in known}
    return RubricDimension(
        name=data["name"],
        evaluator=data["evaluator"],
        description=data.get("description", ""),
        scale=data.get("scale", "pass_fail"),
        field=data.get("field"),
        rule=data.get("rule"),
        threshold=data.get("threshold"),
        required=bool(data.get("required", True)),
        requires_human_review=bool(data.get("requires_human_review", False)),
        prompt=data.get("prompt"),
        options=options,
    )
