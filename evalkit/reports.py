from __future__ import annotations

import html
from pathlib import Path

from evalkit.metrics import calculate_metrics
from evalkit.storage import EvalStore


def render_html_report(store: EvalStore, run_id: str, output_path: str | Path) -> Path:
    run = store.run(run_id)
    case_rows = store.case_rows(run_id)
    dimension_rows = store.dimension_rows(run_id)
    human_rows = store.human_review_rows(run_id)
    signal_rows = store.review_signal_rows(run_id)
    finding_rows = store.finding_rows(run_id)
    target_rows = store.eval_target_rows(run_id)
    metrics = calculate_metrics(case_rows, dimension_rows, human_rows)
    output = Path(output_path)
    output.write_text(_html(run, case_rows, dimension_rows, human_rows, signal_rows, finding_rows, target_rows, metrics))
    return output


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
    <h2>Results</h2>
    <table>
      <thead><tr><th>Case</th><th>Dimension</th><th>Status</th><th>Score</th><th>Rationale</th></tr></thead>
      <tbody>{result_table}</tbody>
    </table>
  </main>
</body>
</html>
"""


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
