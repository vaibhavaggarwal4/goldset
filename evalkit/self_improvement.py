from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import yaml

from evalkit.errors import UserFacingError
from evalkit.models import EvalTarget, Finding, ReviewSignal
from evalkit.storage import EvalStore


def extract_review_signals(store: EvalStore, run_id: str) -> list[int]:
    """Turn human reviews into structured learning signals."""

    store.run(run_id)
    store.clear_review_signals(run_id)
    machine_results = {
        (row["case_id"], row["dimension_name"]): _from_int(row["passed"])
        for row in store.dimension_rows(run_id)
    }
    signal_ids: list[int] = []
    for row in store.human_review_rows(run_id):
        machine_passed = machine_results.get((row["case_id"], row["dimension_name"]))
        signal_type = _signal_type(
            machine_passed=machine_passed,
            human_passed=bool(row["passed"]),
            correction=row["correction"] or "",
            rubric_issue=bool(row["rubric_issue"]),
        )
        if signal_type == "approval":
            continue
        signal = ReviewSignal(
            run_id=run_id,
            case_id=row["case_id"],
            dimension_name=row["dimension_name"],
            machine_passed=machine_passed,
            human_passed=bool(row["passed"]),
            reviewer=row["reviewer"],
            notes=row["notes"] or "",
            correction=row["correction"] or "",
            failure_reason=_failure_reason(row, machine_passed),
            signal_type=signal_type,
        )
        signal_ids.append(store.save_review_signal(signal))
    return signal_ids


def generate_findings(store: EvalStore, run_id: str, min_cases: int = 1) -> list[int]:
    """Group recurring review signals into actionable findings."""

    store.run(run_id)
    store.clear_findings(run_id)
    signals = store.review_signal_rows(run_id)
    if not signals:
        raise UserFacingError(
            "No review signals found for this run.\n"
            "Fix: submit human reviews, then run evalkit signals before evalkit findings."
        )

    groups: dict[tuple[str, str], list] = defaultdict(list)
    for signal in signals:
        groups[(signal["dimension_name"], signal["failure_reason"] or "unspecified")].append(signal)

    finding_ids: list[int] = []
    for (dimension_name, failure_reason), rows in sorted(groups.items()):
        if len(rows) < min_cases:
            continue
        finding = Finding(
            title=_finding_title(dimension_name, failure_reason, len(rows)),
            dimension_name=dimension_name,
            failure_reason=failure_reason,
            case_count=len(rows),
            signal_ids=[int(row["id"]) for row in rows],
        )
        finding_ids.append(store.save_finding(run_id, finding))

    if not finding_ids:
        raise UserFacingError(
            f"No findings met the threshold of min_cases={min_cases}.\n"
            "Fix: lower --min-cases or collect more human reviews."
        )
    return finding_ids


def create_eval_target(store: EvalStore, finding_id: int, owner: str, output_dir: str | Path | None = None) -> tuple[int, Path | None]:
    finding = store.finding(finding_id)
    run_id = finding["run_id"]
    signal_ids = set(json.loads(finding["signal_ids_json"]))
    signals = [row for row in store.review_signal_rows(run_id) if int(row["id"]) in signal_ids]
    if not signals:
        raise UserFacingError(
            f"Finding {finding_id} has no available signals.\n"
            "Fix: rerun evalkit signals and evalkit findings for this database."
        )

    target = EvalTarget(
        finding_id=finding_id,
        title=finding["title"],
        success_criteria=[
            f"Improve pass rate for dimension '{finding['dimension_name']}' on the regression cases.",
            "Do not reduce overall pass rate on the original evaluation run.",
            "Preserve human review as the final gate for ambiguous brand or strategy judgments.",
        ],
        regression_cases=[row["case_id"] for row in signals],
        owner=owner,
    )
    target_id = store.save_eval_target(run_id, target)
    export_path = _export_target(store, run_id, finding_id, target_id, target, signals, output_dir) if output_dir else None
    return target_id, export_path


def _export_target(
    store: EvalStore,
    run_id: str,
    finding_id: int,
    target_id: int,
    target: EvalTarget,
    signals: list,
    output_dir: str | Path,
) -> Path:
    root = Path(output_dir) / f"target-{target_id:04d}-finding-{finding_id:04d}"
    root.mkdir(parents=True, exist_ok=True)
    cases_by_id = {row["case_id"]: row for row in store.case_rows(run_id)}
    task = {
        "target_id": target_id,
        "finding_id": finding_id,
        "title": target.title,
        "owner": target.owner,
        "success_criteria": target.success_criteria,
        "regression_cases": target.regression_cases,
    }
    (root / "task.yaml").write_text(yaml.safe_dump(task, sort_keys=False))
    (root / "README.md").write_text(_target_readme(task))
    with (root / "cases.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "dimension_name",
                "input",
                "artifact_content",
                "human_notes",
                "human_correction",
                "failure_reason",
            ],
        )
        writer.writeheader()
        for signal in signals:
            case = cases_by_id.get(signal["case_id"])
            writer.writerow(
                {
                    "case_id": signal["case_id"],
                    "dimension_name": signal["dimension_name"],
                    "input": case["input_text"] if case else "",
                    "artifact_content": case["artifact_content"] if case else "",
                    "human_notes": signal["notes"],
                    "human_correction": signal["correction"],
                    "failure_reason": signal["failure_reason"],
                }
            )
    return root


def _signal_type(machine_passed: bool | None, human_passed: bool, correction: str, rubric_issue: bool) -> str:
    if rubric_issue:
        return "rubric_issue"
    if correction.strip():
        return "human_correction"
    if machine_passed is not None and machine_passed != human_passed:
        return "human_machine_disagreement"
    if not human_passed:
        return "human_failure"
    return "approval"


def _failure_reason(row, machine_passed: bool | None) -> str:
    if row["failure_reason"]:
        return str(row["failure_reason"])
    if row["rubric_issue"]:
        return "rubric_needs_refinement"
    if machine_passed is not None and bool(row["passed"]) != machine_passed:
        return "human_machine_disagreement"
    if not row["passed"]:
        return "human_rejected_output"
    if row["correction"]:
        return "human_corrected_output"
    return "unspecified"


def _finding_title(dimension_name: str, failure_reason: str, count: int) -> str:
    return f"{dimension_name}: {failure_reason.replace('_', ' ')} ({count} signal{'s' if count != 1 else ''})"


def _from_int(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _target_readme(task: dict) -> str:
    criteria = "\n".join(f"- {item}" for item in task["success_criteria"])
    cases = "\n".join(f"- {item}" for item in task["regression_cases"])
    return f"""# {task['title']}

This eval target was generated from human review signals.

## Success Criteria

{criteria}

## Regression Cases

{cases}

## Suggested Workflow

1. Inspect `cases.csv`.
2. Determine whether the failure belongs in the prompt, rubric, workflow, model routing, or product UX.
3. Make a small targeted change.
4. Rerun the original eval and compare results.
5. Keep ambiguous brand or strategy calls routed to human review.
"""
