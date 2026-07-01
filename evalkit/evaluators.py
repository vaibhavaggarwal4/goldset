from __future__ import annotations

import json

from evalkit.logic import run_logic_check
from evalkit.models import DimensionResult, EvalCase, EvaluationResult, Rubric, RubricDimension
from evalkit.providers.base import LLMProvider


class EvaluationEngine:
    def __init__(self, provider: LLMProvider, model: str | None = None) -> None:
        self.provider = provider
        self.model = model

    def evaluate_cases(self, cases: list[EvalCase], rubric: Rubric) -> list[EvaluationResult]:
        return [self.evaluate_case(case, rubric) for case in cases]

    def evaluate_case(self, case: EvalCase, rubric: Rubric) -> EvaluationResult:
        results = [self.evaluate_dimension(case, dimension) for dimension in rubric.dimensions]
        return EvaluationResult(case=case, dimension_results=results)

    def evaluate_dimension(self, case: EvalCase, dimension: RubricDimension) -> DimensionResult:
        if dimension.evaluator == "logic":
            passed, rationale, details = run_logic_check(case, dimension)
            return DimensionResult(
                case_id=case.case_id,
                dimension_name=dimension.name,
                evaluator="logic",
                passed=passed,
                score=1.0 if passed else 0.0,
                rationale=rationale,
                details=details,
                requires_human_review=dimension.requires_human_review,
            )

        if dimension.evaluator == "llm_judge":
            payload = self.provider.judge_json(
                system_prompt=_system_prompt(),
                user_prompt=_judge_prompt(case, dimension),
                model=self.model,
            )
            passed = _coerce_bool(payload.get("passed"))
            return DimensionResult(
                case_id=case.case_id,
                dimension_name=dimension.name,
                evaluator=f"llm_judge:{self.provider.name}",
                passed=passed,
                score=_coerce_score(payload.get("score")),
                rationale=str(payload.get("rationale", "")),
                details={"raw": payload},
                requires_human_review=dimension.requires_human_review,
            )

        if dimension.evaluator == "human_review":
            return DimensionResult(
                case_id=case.case_id,
                dimension_name=dimension.name,
                evaluator="human_review",
                passed=None,
                score=None,
                rationale="Awaiting human review.",
                requires_human_review=True,
            )

        raise ValueError(f"Unsupported evaluator '{dimension.evaluator}'.")


def _system_prompt() -> str:
    return (
        "You are a careful marketing quality evaluator. Return only valid JSON with keys: "
        "passed boolean, score number from 1 to 5, rationale string. Be strict and concise."
    )


def _judge_prompt(case: EvalCase, dimension: RubricDimension) -> str:
    target = case.artifact.fields.get(dimension.field) if dimension.field else case.artifact.content
    context = {
        "case_id": case.case_id,
        "input": case.input_text,
        "metadata": case.metadata,
        "artifact_fields": case.artifact.fields,
        "artifact_content": case.artifact.content,
    }
    return (
        f"Evaluate this marketing artifact on one rubric dimension.\n\n"
        f"Dimension: {dimension.name}\n"
        f"Description: {dimension.description}\n"
        f"Scale: {dimension.scale}\n"
        f"Additional instructions: {dimension.prompt or 'None'}\n\n"
        f"Target text:\n{target or ''}\n\n"
        f"Full context JSON:\n{json.dumps(context, indent=2)}"
    )


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "pass", "passed", "yes"}
    return bool(value)


def _coerce_score(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
