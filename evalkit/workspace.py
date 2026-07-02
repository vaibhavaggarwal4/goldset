from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from pathlib import Path

from evalkit.errors import UserFacingError


SUPPORTED_SURFACES = {
    "lifecycle_email": {
        "template": "templates/rubrics/lifecycle_email.yaml",
        "sample": "examples/lifecycle_email/sample.csv",
        "golden": "examples/golden_sets/lifecycle_email_golden_set.csv",
        "outcomes": "examples/outcomes/lifecycle_email_outcomes.csv",
        "dimensions": [
            ("subject_line_length", "logic", "Subject line should fit inbox scanning constraints."),
            ("body_length", "logic", "Email body should stay concise enough for lifecycle use."),
            ("has_cta", "logic", "Email includes a clear next action."),
            ("clarity", "llm_judge", "Message communicates the core idea quickly and concretely."),
            ("audience_fit", "llm_judge", "Message fits the audience and lifecycle stage."),
            ("brand_voice", "human_review", "Reviewer confirms the output feels on-brand."),
        ],
    },
    "paid_social_ad": {
        "template": "templates/rubrics/paid_social_ad.yaml",
        "sample": "examples/sample_data/marketing_outputs.csv",
        "golden": None,
        "outcomes": None,
        "dimensions": [
            ("primary_text_length", "logic", "Primary text stays concise for the placement."),
            ("has_cta", "logic", "Ad includes a clear next action."),
            ("clarity", "llm_judge", "Ad communicates one clear idea."),
            ("audience_fit", "llm_judge", "Ad matches the target customer and job-to-be-done."),
            ("offer_quality", "human_review", "Reviewer confirms the offer is compelling enough to test."),
        ],
    },
    "landing_page": {
        "template": "templates/rubrics/landing_page.yaml",
        "sample": "examples/sample_data/marketing_outputs.csv",
        "golden": None,
        "outcomes": None,
        "dimensions": [
            ("headline_length", "logic", "Headline stays short enough to scan above the fold."),
            ("promise_clarity", "llm_judge", "Page makes a specific and credible customer promise."),
            ("objection_handling", "llm_judge", "Page addresses a likely buyer concern."),
            ("proof", "human_review", "Reviewer confirms the page has sufficient proof for the claim."),
            ("brand_voice", "human_review", "Reviewer confirms the page feels on-brand."),
        ],
    },
    "seo_content_brief": {
        "template": "templates/rubrics/basic_marketing_quality.yaml",
        "sample": "examples/sample_data/marketing_outputs.csv",
        "golden": None,
        "outcomes": None,
        "dimensions": [
            ("target_keyword_present", "logic", "Brief includes the target keyword."),
            ("search_intent_fit", "llm_judge", "Brief matches the likely search intent."),
            ("differentiation", "llm_judge", "Brief has a distinct POV rather than generic advice."),
            ("usefulness", "human_review", "Reviewer confirms the brief would help a writer create useful content."),
        ],
    },
    "general": {
        "template": "templates/rubrics/basic_marketing_quality.yaml",
        "sample": "examples/sample_data/marketing_outputs.csv",
        "golden": None,
        "outcomes": None,
        "dimensions": [
            ("output_length", "logic", "Output is long enough to evaluate."),
            ("has_cta", "logic", "Output includes a clear next action when appropriate."),
            ("clarity", "llm_judge", "Output communicates the core message clearly."),
            ("audience_fit", "llm_judge", "Output fits the stated audience and campaign goal."),
            ("brand_voice", "human_review", "Reviewer confirms the output feels on-brand."),
        ],
    },
}


@dataclass(frozen=True)
class WorkspaceFiles:
    root: Path
    rubric: Path
    data: Path
    golden_set: Path
    outcomes: Path
    readme: Path


def suggest_dimensions(surface: str) -> list[tuple[str, str, str]]:
    config = _surface_config(surface)
    return list(config["dimensions"])


def create_workspace(surface: str, name: str, output_dir: str | Path, force: bool = False) -> WorkspaceFiles:
    config = _surface_config(surface)
    root = Path(output_dir) / name
    if root.exists() and any(root.iterdir()) and not force:
        raise UserFacingError(
            f"Workspace already exists and is not empty: {root}\n"
            "Fix: choose a different --name/--output-dir, or pass --force to overwrite generated files."
        )
    root.mkdir(parents=True, exist_ok=True)

    rubric_path = root / "rubric.yaml"
    data_path = root / "data.csv"
    golden_path = root / "golden_set.csv"
    outcomes_path = root / "outcomes.csv"
    readme_path = root / "README.md"

    _copy(config["template"], rubric_path)
    _copy(config["sample"], data_path)
    if config["golden"]:
        _copy(config["golden"], golden_path)
    else:
        _write_golden_template(golden_path)
    if config["outcomes"]:
        _copy(config["outcomes"], outcomes_path)
    else:
        _write_outcomes_template(outcomes_path)
    readme_path.write_text(_workspace_readme(surface, name))
    return WorkspaceFiles(root, rubric_path, data_path, golden_path, outcomes_path, readme_path)


def _surface_config(surface: str) -> dict:
    if surface not in SUPPORTED_SURFACES:
        supported = ", ".join(sorted(SUPPORTED_SURFACES))
        raise UserFacingError(f"Unsupported surface '{surface}'. Supported surfaces: {supported}.")
    return SUPPORTED_SURFACES[surface]


def _copy(source: str | None, destination: Path) -> None:
    if not source:
        return
    shutil.copyfile(source, destination)


def _write_golden_template(path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "dimension_name", "expected_passed", "expected_score", "labeler", "notes"])
        writer.writerow(["example-001", "clarity", "true", "5", "expert", "Replace this row with your expert label."])


def _write_outcomes_template(path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "ctr", "conversion_rate", "reply_rate"])
        writer.writerow(["example-001", "0.05", "0.10", "0.01"])


def _workspace_readme(surface: str, name: str) -> str:
    return f"""# {name}

This folder was generated by `evalkit init` for `{surface}`. The commands below assume you run them from the parent directory that contains this folder.

## Recommended Workflow

1. Edit `rubric.yaml` until it reflects what good means for your team.
2. Replace or edit `data.csv` with AI-generated marketing outputs.
3. Add expert labels to `golden_set.csv`. You can skip this at first, but evaluator quality will be harder to trust.
4. Run an eval:

```bash
evalkit run --rubric {name}/rubric.yaml --input {name}/data.csv --db {name}/evalkit.sqlite --report {name}/reports/report.html
```

5. Review failures:

```bash
evalkit review --db {name}/evalkit.sqlite --run-id latest
```

6. Learn from feedback:

```bash
evalkit learn --db {name}/evalkit.sqlite --run-id latest
```

7. Calibrate against the golden set:

```bash
evalkit calibrate --db {name}/evalkit.sqlite --run-id latest --golden-set {name}/golden_set.csv
```

8. Backtest against labels and outcomes:

```bash
evalkit backtest --rubric {name}/rubric.yaml --input {name}/data.csv --golden-set {name}/golden_set.csv --outcomes {name}/outcomes.csv --db {name}/backtest.sqlite --report {name}/reports/backtest-report.html
```
"""
