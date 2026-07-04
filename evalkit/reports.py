from __future__ import annotations

import html
from pathlib import Path
import re

from evalkit.metrics import calculate_metrics
from evalkit.storage import EvalStore


def render_html_report(store: EvalStore, run_id: str, output_path: str | Path | None = None) -> Path:
    run = store.run(run_id)
    case_rows = store.case_rows(run_id)
    dimension_rows = store.dimension_rows(run_id)
    human_rows = store.human_review_rows(run_id)
    signal_rows = store.review_signal_rows(run_id)
    finding_rows = store.finding_rows(run_id)
    target_rows = store.eval_target_rows(run_id)
    metrics = calculate_metrics(case_rows, dimension_rows, human_rows)
    output = _available_report_path(output_path, run)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_html(run, case_rows, dimension_rows, human_rows, signal_rows, finding_rows, target_rows, metrics))
    return output


def _available_report_path(output_path: str | Path | None, run) -> Path:
    if output_path:
        requested = Path(output_path)
    else:
        suite_slug = _slugify(run["suite_name"]) or "eval-report"
        requested = Path("reports") / f"{suite_slug}-{run['id'][:8]}.html"
    if not requested.exists():
        return requested

    stem = requested.stem
    suffix = requested.suffix or ".html"
    parent = requested.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64].strip("-")


def _html(run, case_rows, dimension_rows, human_rows, signal_rows, finding_rows, target_rows, metrics: dict) -> str:
    dimension_table = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td>{_pct(values['pass_rate'])}</td>"
        f"<td>{values['evaluated']}/{values['total']}</td><td>{values['needs_human_review']}</td></tr>"
        for name, values in metrics["by_dimension"].items()
    )
    result_table = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['case_id'])}</td>"
        f"<td>{html.escape(row['dimension_name'])}</td>"
        f"<td>{_status(row['passed'])}</td>"
        f"<td>{'' if row['score'] is None else row['score']}</td>"
        f"<td>{html.escape(row['rationale'] or '')}</td>"
        "</tr>"
        for row in dimension_rows
    )
    finding_table = "\n".join(
        "<tr>"
        f"<td>{row['id']}</td>"
        f"<td>{html.escape(row['title'])}</td>"
        f"<td>{html.escape(row['dimension_name'])}</td>"
        f"<td>{row['case_count']}</td>"
        f"<td>{html.escape(row['status'])}</td>"
        "</tr>"
        for row in finding_rows
    )
    if not finding_table:
        finding_table = '<tr><td colspan="5">No findings yet. Submit human reviews, then run <code>evalkit learn</code>.</td></tr>'
    generator_brief = _generator_improvement_brief(finding_rows, signal_rows, dimension_rows)
    review_count = len(human_rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evaluation Report</title>
  <style>
    body {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; color: #111827; background: #f8fafc; }}
    header {{ background: #0f172a; color: white; padding: 32px 40px; }}
    main {{ padding: 28px 40px 48px; max-width: 1180px; }}
    h1, h2 {{ margin: 0 0 12px; letter-spacing: 0; }}
    .meta {{ color: #cbd5e1; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; margin: 24px 0; }}
    .metric {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; }}
    .metric strong {{ display: block; font-size: 28px; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e5e7eb; margin: 12px 0 28px; }}
    th, td {{ text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid #e5e7eb; font-size: 14px; }}
    th {{ background: #f1f5f9; }}
    .pass {{ color: #047857; font-weight: 700; }}
    .fail {{ color: #b91c1c; font-weight: 700; }}
    .pending {{ color: #92400e; font-weight: 700; }}
    .brief {{ background: white; border: 1px solid #dbe3ea; border-radius: 8px; padding: 18px; margin: 12px 0 28px; }}
    .brief p {{ color: #475569; line-height: 1.55; }}
    .brief pre {{ white-space: pre-wrap; word-break: break-word; background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; font-size: 13px; line-height: 1.5; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(run['suite_name'])}</h1>
    <div class="meta">Run {html.escape(run['id'])} · Rubric {html.escape(run['rubric_name'])} v{html.escape(run['rubric_version'])} · Provider {html.escape(run['provider'])}</div>
  </header>
  <main>
    <section class="grid">
      <div class="metric">Cases<strong>{metrics['total_cases']}</strong></div>
      <div class="metric">Overall Pass Rate<strong>{_pct(metrics['pass_rate'])}</strong></div>
      <div class="metric">Human Reviews<strong>{review_count}</strong></div>
      <div class="metric">Human/Machine Agreement<strong>{_pct(metrics['human_machine_agreement'])}</strong></div>
      <div class="metric">Review Signals<strong>{len(signal_rows)}</strong></div>
      <div class="metric">Findings<strong>{len(finding_rows)}</strong></div>
      <div class="metric">Eval Targets<strong>{len(target_rows)}</strong></div>
    </section>
    <h2>Dimension Pass Rates</h2>
    <table>
      <thead><tr><th>Dimension</th><th>Pass Rate</th><th>Evaluated</th><th>Needs Human Review</th></tr></thead>
      <tbody>{dimension_table}</tbody>
    </table>
    <h2>Learning Loop</h2>
    <table>
      <thead><tr><th>ID</th><th>Finding</th><th>Dimension</th><th>Cases</th><th>Status</th></tr></thead>
      <tbody>{finding_table}</tbody>
    </table>
    <h2>Generator Improvement Brief</h2>
    <section class="brief">
      <p>Give this to the upstream AI system, prompt owner, or workflow builder to improve the next generation pass. Goldset measures quality; this brief turns eval failures into generator-side changes to try.</p>
      <pre>{html.escape(generator_brief)}</pre>
    </section>
    <h2>Results</h2>
    <table>
      <thead><tr><th>Case</th><th>Dimension</th><th>Status</th><th>Score</th><th>Rationale</th></tr></thead>
      <tbody>{result_table}</tbody>
    </table>
  </main>
</body>
</html>
"""


def _generator_improvement_brief(finding_rows, signal_rows, dimension_rows) -> str:
    lines = [
        "Use this with the upstream AI marketing generator.",
        "",
        "Goal: improve the initial generated campaign outputs before they reach Goldset.",
        "",
        "Recommended generator-side changes:",
    ]
    recommendations = _finding_recommendations(finding_rows)
    if not recommendations:
        recommendations = _signal_recommendations(signal_rows)
    if not recommendations:
        recommendations = _failure_recommendations(dimension_rows)
    if not recommendations:
        recommendations = [
            "- No clear generator failures were found in this run. Keep the current generator unchanged, then rerun Goldset on the next batch to watch for regressions."
        ]
    lines.extend(recommendations)
    lines.extend(
        [
            "",
            "Suggested loop:",
            "1. Apply the changes to the upstream prompt, context, examples, retrieval inputs, model route, or generation workflow.",
            "2. Generate a fresh batch of campaign outputs.",
            "3. Rerun the same Goldset rubric on the new outputs.",
            "4. Compare pass rate, failed dimensions, human review notes, and business outcome correlation before adopting the change.",
        ]
    )
    return "\n".join(lines)


def _finding_recommendations(finding_rows) -> list[str]:
    rows = list(finding_rows)[:8]
    return [
        "- "
        + _generator_action(
            dimension=row["dimension_name"],
            issue=row["failure_reason"],
            evidence=f"{row['case_count']} reviewed signal(s)",
        )
        for row in rows
    ]


def _signal_recommendations(signal_rows) -> list[str]:
    grouped: dict[tuple[str, str], int] = {}
    for row in signal_rows:
        key = (row["dimension_name"], row["failure_reason"] or row["signal_type"] or "review feedback")
        grouped[key] = grouped.get(key, 0) + 1
    return [
        "- " + _generator_action(dimension=dimension, issue=issue, evidence=f"{count} review signal(s)")
        for (dimension, issue), count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]


def _failure_recommendations(dimension_rows) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for row in dimension_rows:
        if row["passed"] == 0:
            grouped.setdefault(row["dimension_name"], []).append(row["rationale"] or "machine failed this dimension")
    return [
        "- " + _generator_action(dimension=dimension, issue=_common_issue(rationales), evidence=f"{len(rationales)} failed case(s)")
        for dimension, rationales in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:8]
    ]


def _generator_action(*, dimension: str, issue: str, evidence: str) -> str:
    clean_dimension = dimension.replace("_", " ")
    clean_issue = (issue or "quality gap").replace("_", " ")
    return (
        f"For '{clean_dimension}', address '{clean_issue}' ({evidence}). "
        f"Update the generator instructions to explicitly satisfy this criterion before final output; add 1-2 strong examples that pass it; "
        f"and add a self-check step that rejects drafts likely to fail '{clean_dimension}'."
    )


def _common_issue(rationales: list[str]) -> str:
    for rationale in rationales:
        if rationale:
            return rationale[:180]
    return "machine failed this dimension"


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _status(value: int | None) -> str:
    if value is None:
        return '<span class="pending">Pending</span>'
    if value == 1:
        return '<span class="pass">Pass</span>'
    return '<span class="fail">Fail</span>'
