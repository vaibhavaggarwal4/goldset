from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any


@dataclass(frozen=True)
class EvalArtifact:
    """The marketing artifact being evaluated."""

    content: str
    artifact_type: str = "text"
    fields: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class EvalCase:
    """One input/output example plus context."""

    case_id: str
    input_text: str
    artifact: EvalArtifact
    metadata: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class RubricDimension:
    """One quality dimension in a rubric."""

    name: str
    evaluator: str
    description: str
    scale: str = "pass_fail"
    field: str | None = None
    rule: str | None = None
    threshold: int | float | None = None
    required: bool = True
    requires_human_review: bool = False
    prompt: str | None = None
    options: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class Rubric:
    """A reusable quality standard for a marketing surface."""

    name: str
    version: str
    artifact_type: str
    dimensions: list[RubricDimension]
    description: str = ""


@dataclass(frozen=True)
class DimensionResult:
    """The result for one rubric dimension."""

    case_id: str
    dimension_name: str
    evaluator: str
    passed: bool | None
    score: float | None = None
    rationale: str = ""
    details: dict[str, Any] = dc_field(default_factory=dict)
    requires_human_review: bool = False


@dataclass(frozen=True)
class EvaluationResult:
    """All dimension results for one case."""

    case: EvalCase
    dimension_results: list[DimensionResult]

    @property
    def passed(self) -> bool | None:
        required = [result for result in self.dimension_results if result.passed is not None]
        if not required:
            return None
        return all(result.passed for result in required)


@dataclass(frozen=True)
class ReviewSignal:
    """A structured learning signal created from expert review."""

    run_id: str
    case_id: str
    dimension_name: str
    machine_passed: bool | None
    human_passed: bool
    reviewer: str
    notes: str = ""
    correction: str = ""
    failure_reason: str = ""
    signal_type: str = "needs_improvement"


@dataclass(frozen=True)
class Finding:
    """A recurring failure pattern that can become an eval target."""

    title: str
    dimension_name: str
    failure_reason: str
    case_count: int
    signal_ids: list[int]
    status: str = "open"


@dataclass(frozen=True)
class EvalTarget:
    """A bounded target for improving prompts, rubrics, workflows, or model routing."""

    finding_id: int
    title: str
    success_criteria: list[str]
    regression_cases: list[str]
    owner: str = "unassigned"
