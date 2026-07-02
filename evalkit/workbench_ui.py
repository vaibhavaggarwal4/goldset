from __future__ import annotations

from email import policy
from email.parser import BytesParser
import html
import json
import re
import socketserver
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from evalkit.errors import UserFacingError
from evalkit.evaluators import EvaluationEngine
from evalkit.golden import load_golden_set, load_outcomes
from evalkit.loaders import load_cases_from_csv
from evalkit.metrics import calculate_metrics, calculate_outcome_correlations, calculate_reliability_metrics
from evalkit.providers.factory import make_provider
from evalkit.reports import render_html_report
from evalkit.rubrics import load_rubric
from evalkit.self_improvement import extract_review_signals, generate_findings
from evalkit.storage import EvalStore


def serve_workbench(db_path: str | Path, host: str, port: int) -> None:
    handler = _make_handler(Path(db_path))
    try:
        server = LocalWorkbenchServer((host, port), handler)
    except OSError as exc:
        raise UserFacingError(
            f"Could not start the workbench on {host}:{port}.\n"
            "Fix: choose another port with --port, for example --port 8770."
        ) from exc
    print(f"Goldset Workbench: http://{host}:{port}/", flush=True)
    server.serve_forever()


class LocalWorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _make_handler(db_path: Path):
    class WorkbenchHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/":
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            self._send_html(_render_home(db_path, query))

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("content-length", "0"))
                payload = _parse_payload(self.headers.get("content-type", ""), self.rfile.read(length), db_path)
                if self.path == "/actions/run":
                    notice, run_id = _run_eval(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/backtest":
                    notice, run_id = _run_backtest(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/report":
                    notice, run_id = _render_report(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/review":
                    notice, run_id = _save_review(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/learn":
                    notice, run_id = _learn(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/calibrate":
                    notice, run_id = _calibrate(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                elif self.path == "/actions/outcomes":
                    notice, run_id = _outcomes(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "notice": notice})
                else:
                    self.send_error(404)
            except UserFacingError as exc:
                self._send_html(_render_error(db_path, str(exc)))
            except Exception as exc:
                self._send_html(_render_error(db_path, f"Unexpected error: {exc}"))

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, path: str, params: dict[str, str]) -> None:
            self.send_response(303)
            self.send_header("Location", f"{path}?{urlencode(params)}")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return

    return WorkbenchHandler


def _run_eval(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    rubric_path = _file_or_value(payload, "rubric_file", "rubric_path")
    input_path = _file_or_value(payload, "input_file", "input_path")
    suite_name = _value(payload, "suite_name") or "Marketing Evaluation"
    provider_name = _value(payload, "provider") or "heuristic"
    model = _value(payload, "model") or None
    report_path = _value(payload, "report_path") or None

    rubric = load_rubric(rubric_path)
    cases = load_cases_from_csv(input_path, artifact_type=rubric.artifact_type)
    provider = make_provider(provider_name)
    engine = EvaluationEngine(provider=provider, model=model)
    store = EvalStore(db_path)
    run_id = store.create_run(
        suite_name=suite_name,
        rubric=rubric,
        provider=provider.name,
        model=model,
        input_path=input_path,
    )
    store.save_results(run_id, engine.evaluate_cases(cases, rubric))
    report = render_html_report(store, run_id, report_path)
    return f"Run complete. Evaluated {len(cases)} case(s). Report: {report.resolve().as_uri()}", run_id


def _run_backtest(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    notice, run_id = _run_eval(db_path, payload)
    golden_path = _file_or_value(payload, "golden_file", "golden_set")
    if golden_path:
        store = EvalStore(db_path)
        reliability = calculate_reliability_metrics(store.dimension_rows(run_id), load_golden_set(golden_path))
        accuracy = _pct(reliability["overall"]["accuracy"])
        notice = f"{notice} Reliability accuracy: {accuracy}."
    return notice, run_id


def _render_report(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    report_path = _value(payload, "report_path") or None
    report = render_html_report(store, run_id, report_path)
    return f"Report generated: {report.resolve().as_uri()}", run_id


def _save_review(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    score = _value(payload, "score")
    store.save_human_review(
        run_id=run_id,
        case_id=_value(payload, "case_id"),
        dimension_name=_value(payload, "dimension_name"),
        reviewer=_value(payload, "reviewer") or "reviewer",
        passed=_value(payload, "passed") == "pass",
        score=float(score) if score else None,
        notes=_value(payload, "notes"),
        correction=_value(payload, "correction"),
        failure_reason=_value(payload, "failure_reason"),
        rubric_issue=_value(payload, "rubric_issue") == "on",
    )
    return "Review saved.", run_id


def _learn(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    signal_ids = extract_review_signals(store, run_id)
    finding_ids = generate_findings(store, run_id, min_cases=1)
    return f"Learning loop updated. Signals: {len(signal_ids)}. Findings: {len(finding_ids)}.", run_id


def _calibrate(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    labels = load_golden_set(_file_or_value(payload, "golden_file", "golden_set"))
    reliability = calculate_reliability_metrics(store.dimension_rows(run_id), labels)
    overall = reliability["overall"]
    return (
        "Calibration complete. "
        f"Matched labels: {reliability['matched_labels']}/{reliability['total_golden_labels']}. "
        f"Accuracy: {_pct(overall['accuracy'])}. Precision: {_pct(overall['precision'])}. Recall: {_pct(overall['recall'])}.",
        run_id,
    )


def _outcomes(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    correlations = calculate_outcome_correlations(
        store.case_rows(run_id),
        store.dimension_rows(run_id),
        load_outcomes(_file_or_value(payload, "outcomes_file", "outcomes")),
    )
    metric_parts = [
        f"{name}: r={_num(values['pearson'])}, n={values['n']}"
        for name, values in correlations["overall_pass"].items()
    ]
    return f"Outcome correlation complete. Overall pass: {'; '.join(metric_parts) or 'no numeric outcome metrics found'}.", run_id


def _render_home(db_path: Path, query: dict[str, list[str]]) -> str:
    store = EvalStore(db_path)
    runs = _run_rows(store)
    run_id = _selected_run_id(store, runs, query)
    notice = _value(query, "notice")
    selected = store.run(run_id) if run_id else None
    case_rows = store.case_rows(run_id) if run_id else []
    dimension_rows = store.dimension_rows(run_id) if run_id else []
    human_rows = store.human_review_rows(run_id) if run_id else []
    metrics = calculate_metrics(case_rows, dimension_rows, human_rows) if run_id else None
    findings = store.finding_rows(run_id) if run_id else []
    review_rows = _review_queue(case_rows, dimension_rows, human_rows) if run_id else []
    failures = [row for row in dimension_rows if row["passed"] == 0][:12]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Goldset Workbench</title>
  <style>{_css()}</style>
</head>
<body>
  <aside>
    <div class="brand">Goldset</div>
    <nav>
      <a href="#run">Run</a>
      <a href="#results">Results</a>
      <a href="#review">Review</a>
      <a href="#learn">Learn</a>
      <a href="#backtest">Backtest</a>
    </nav>
    <div class="path-label">Database</div>
    <code>{html.escape(str(db_path.resolve()))}</code>
  </aside>
  <main>
    <section class="hero">
      <div>
        <p class="eyebrow">Local AI marketing evals workbench</p>
        <h1>Evaluate, review, and improve AI-generated marketing work.</h1>
      </div>
      <div class="hero-actions">
        <form method="post" action="/actions/report">
          <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
          <button {"disabled" if not run_id else ""}>Generate report</button>
        </form>
      </div>
    </section>
    {_notice(notice)}
    <section class="metrics">{_metric_cards(metrics, len(human_rows), len(findings))}</section>
    <div class="grid two">
      <section id="run" class="panel">{_run_form()}</section>
      <section class="panel">{_runs_panel(runs, run_id)}</section>
    </div>
    <section id="results" class="panel">{_results_panel(selected, metrics, failures)}</section>
    <section id="review" class="panel">{_review_panel(run_id, review_rows)}</section>
    <section id="learn" class="panel">{_learn_panel(run_id, findings)}</section>
    <section id="backtest" class="panel">{_backtest_panel(run_id)}</section>
  </main>
</body>
</html>"""


def _render_error(db_path: Path, message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Goldset Workbench Error</title><style>{_css()}</style></head>
<body><main class="solo"><section class="panel error"><h1>Something needs attention</h1><pre>{html.escape(message)}</pre><a href="/">Back to workbench</a><p class="muted">Database: {html.escape(str(db_path.resolve()))}</p></section></main></body>
</html>"""


def _run_form() -> str:
    return """<h2>Run an eval</h2>
<p class="muted">Start with the sample, then swap in your own rubric and CSV.</p>
<form method="post" action="/actions/run" class="stack" enctype="multipart/form-data">
  <label>Rubric YAML file<input name="rubric_file" type="file" accept=".yaml,.yml"></label>
  <label>Or rubric path<input name="rubric_path" value="examples/lifecycle_email/rubric.yaml"></label>
  <label>Input CSV file<input name="input_file" type="file" accept=".csv,text/csv"></label>
  <label>Or input path<input name="input_path" value="examples/lifecycle_email/sample.csv"></label>
  <label>Suite name<input name="suite_name" value="Lifecycle Email Evaluation"></label>
  <div class="row">
    <label>Provider<select name="provider"><option value="heuristic">heuristic</option><option value="openai">openai</option><option value="ollama">ollama</option></select></label>
    <label>Model<input name="model" placeholder="optional"></label>
  </div>
  <label>Report path<input name="report_path" placeholder="optional, defaults to reports/"></label>
  <button>Run eval</button>
</form>"""


def _runs_panel(runs, selected_run_id: str | None) -> str:
    rows = "\n".join(
        f"""<tr class="{"selected" if row['id'] == selected_run_id else ""}">
  <td><a href="/?run_id={html.escape(row['id'])}">{html.escape(row['suite_name'])}</a><span>{html.escape(row['created_at'][:19])}</span></td>
  <td>{html.escape(row['provider'])}</td>
  <td><code>{html.escape(row['id'][:8])}</code></td>
</tr>"""
        for row in runs
    )
    if not rows:
        rows = '<tr><td colspan="3" class="empty">No runs yet. Run the sample to create your first eval.</td></tr>'
    return f"""<h2>Runs</h2>
<table><thead><tr><th>Suite</th><th>Provider</th><th>Run</th></tr></thead><tbody>{rows}</tbody></table>"""


def _results_panel(selected, metrics: dict | None, failures) -> str:
    if not selected or not metrics:
        return '<h2>Results</h2><p class="empty">Run an eval to see quality metrics.</p>'
    dimensions = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td>{_pct(values['pass_rate'])}</td><td>{values['evaluated']}/{values['total']}</td><td>{values['needs_human_review']}</td></tr>"
        for name, values in metrics["by_dimension"].items()
    )
    failure_rows = "\n".join(
        f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['dimension_name'])}</td><td>{html.escape(row['rationale'] or '')}</td></tr>"
        for row in failures
    )
    if not failure_rows:
        failure_rows = '<tr><td colspan="3" class="empty">No machine failures in this run.</td></tr>'
    return f"""<h2>{html.escape(selected['suite_name'])}</h2>
<p class="muted">Run {html.escape(selected['id'])} · Rubric {html.escape(selected['rubric_name'])} v{html.escape(selected['rubric_version'])}</p>
<h3>Dimension pass rates</h3>
<table><thead><tr><th>Dimension</th><th>Pass rate</th><th>Evaluated</th><th>Human review</th></tr></thead><tbody>{dimensions}</tbody></table>
<h3>Failures to inspect</h3>
<table><thead><tr><th>Case</th><th>Dimension</th><th>Rationale</th></tr></thead><tbody>{failure_rows}</tbody></table>"""


def _review_panel(run_id: str | None, review_rows: list[tuple]) -> str:
    if not run_id:
        return '<h2>Human review</h2><p class="empty">Run an eval to create a review queue.</p>'
    cards = "\n".join(_review_card(run_id, case, dimension) for case, dimension in review_rows[:8])
    if not cards:
        cards = '<p class="empty">No dimensions currently require human review.</p>'
    return f"<h2>Human review queue</h2>{cards}"


def _review_card(run_id: str, case, dimension) -> str:
    fields = json.loads(case["fields_json"] or "{}")
    return f"""<article class="review-card">
  <div>
    <h3>{html.escape(case['case_id'])} · {html.escape(dimension['dimension_name'])}</h3>
    <p class="muted">Machine: {_status(dimension['passed'])} · Score: {dimension['score'] or 'N/A'}</p>
  </div>
  <details><summary>View artifact and rationale</summary>
    <label>Artifact</label><pre>{html.escape(case['artifact_content'] or '')}</pre>
    <label>Fields</label><pre>{html.escape(json.dumps(fields, indent=2))}</pre>
    <label>Rationale</label><pre>{html.escape(dimension['rationale'] or '')}</pre>
  </details>
  <form method="post" action="/actions/review" class="stack compact">
    <input type="hidden" name="run_id" value="{html.escape(run_id)}">
    <input type="hidden" name="case_id" value="{html.escape(case['case_id'])}">
    <input type="hidden" name="dimension_name" value="{html.escape(dimension['dimension_name'])}">
    <div class="row">
      <label>Reviewer<input name="reviewer" value="reviewer"></label>
      <label>Judgment<select name="passed"><option value="pass">Pass</option><option value="fail">Fail</option></select></label>
      <label>Score<input name="score" type="number" min="1" max="5" step="0.5"></label>
    </div>
    <label>Failure reason<input name="failure_reason" placeholder="unclear_cta, off_brand, judge_wrong"></label>
    <label>Correction<textarea name="correction"></textarea></label>
    <label>Notes<textarea name="notes"></textarea></label>
    <label class="inline"><input name="rubric_issue" type="checkbox"> Rubric needs refinement</label>
    <button>Save review</button>
  </form>
</article>"""


def _learn_panel(run_id: str | None, findings) -> str:
    button = f"""<form method="post" action="/actions/learn"><input type="hidden" name="run_id" value="{html.escape(run_id or '')}"><button {"disabled" if not run_id else ""}>Extract signals and findings</button></form>"""
    rows = "\n".join(
        f"<tr><td>{row['id']}</td><td>{html.escape(row['title'])}</td><td>{html.escape(row['dimension_name'])}</td><td>{row['case_count']}</td></tr>"
        for row in findings
    )
    if not rows:
        rows = '<tr><td colspan="4" class="empty">No findings yet. Save reviews, then run the learning loop.</td></tr>'
    return f"""<h2>Learning loop</h2>
<p class="muted">Turn review feedback into recurring findings that can guide prompt, rubric, workflow, or model improvements.</p>
{button}
<table><thead><tr><th>ID</th><th>Finding</th><th>Dimension</th><th>Cases</th></tr></thead><tbody>{rows}</tbody></table>"""


def _backtest_panel(run_id: str | None) -> str:
    disabled = "disabled" if not run_id else ""
    return f"""<h2>Calibration and backtesting</h2>
<div class="grid two">
  <form method="post" action="/actions/calibrate" class="stack" enctype="multipart/form-data">
    <h3>Calibrate selected run</h3>
    <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
    <label>Golden set CSV file<input name="golden_file" type="file" accept=".csv,text/csv"></label>
    <label>Or golden set path<input name="golden_set" value="examples/golden_sets/lifecycle_email_golden_set.csv"></label>
    <button {disabled}>Run calibration</button>
  </form>
  <form method="post" action="/actions/outcomes" class="stack" enctype="multipart/form-data">
    <h3>Outcome correlation</h3>
    <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
    <label>Outcomes CSV file<input name="outcomes_file" type="file" accept=".csv,text/csv"></label>
    <label>Or outcomes path<input name="outcomes" value="examples/outcomes/lifecycle_email_outcomes.csv"></label>
    <button {disabled}>Calculate correlation</button>
  </form>
</div>
<form method="post" action="/actions/backtest" class="stack backtest" enctype="multipart/form-data">
  <h3>Run historical backtest</h3>
  <div class="row">
    <label>Rubric YAML file<input name="rubric_file" type="file" accept=".yaml,.yml"></label>
    <label>Input CSV file<input name="input_file" type="file" accept=".csv,text/csv"></label>
  </div>
  <div class="row">
    <label>Or rubric path<input name="rubric_path" value="examples/lifecycle_email/rubric.yaml"></label>
    <label>Or input path<input name="input_path" value="examples/lifecycle_email/sample.csv"></label>
  </div>
  <div class="row">
    <label>Golden set file<input name="golden_file" type="file" accept=".csv,text/csv"></label>
    <label>Or golden set path<input name="golden_set" value="examples/golden_sets/lifecycle_email_golden_set.csv"></label>
  </div>
  <div class="row">
    <label>Suite name<input name="suite_name" value="Lifecycle Email Backtest"></label>
    <label>Report path<input name="report_path" placeholder="optional, defaults to reports/"></label>
  </div>
  <div class="row">
    <label>Provider<select name="provider"><option value="heuristic">heuristic</option><option value="openai">openai</option><option value="ollama">ollama</option></select></label>
    <label>Model<input name="model" placeholder="optional"></label>
  </div>
  <button>Run backtest</button>
</form>"""


def _metric_cards(metrics: dict | None, review_count: int, finding_count: int) -> str:
    if not metrics:
        values = [("Cases", "0"), ("Pass rate", "N/A"), ("Human reviews", "0"), ("Findings", "0")]
    else:
        values = [
            ("Cases", str(metrics["total_cases"])),
            ("Pass rate", _pct(metrics["pass_rate"])),
            ("Human reviews", str(review_count)),
            ("Findings", str(finding_count)),
        ]
    return "".join(f'<div class="metric"><span>{label}</span><strong>{value}</strong></div>' for label, value in values)


def _review_queue(case_rows, dimension_rows, human_rows) -> list[tuple]:
    cases = {row["case_id"]: row for row in case_rows}
    reviewed = {(row["case_id"], row["dimension_name"]) for row in human_rows}
    rows = []
    for dimension in dimension_rows:
        key = (dimension["case_id"], dimension["dimension_name"])
        if key in reviewed:
            continue
        if dimension["requires_human_review"] == 1 or dimension["passed"] is None:
            case = cases.get(dimension["case_id"])
            if case:
                rows.append((case, dimension))
    return rows


def _run_rows(store: EvalStore):
    return list(store.conn.execute("select * from runs order by created_at desc limit 20"))


def _selected_run_id(store: EvalStore, runs, query: dict[str, list[str]]) -> str | None:
    requested = _value(query, "run_id")
    if requested:
        return requested
    if runs:
        return str(runs[0]["id"])
    return None


def _run_id(store: EvalStore, payload: dict[str, list[str]]) -> str:
    requested = _value(payload, "run_id")
    return requested if requested else store.latest_run_id()


def _value(payload: dict[str, list[str]], key: str) -> str:
    return payload.get(key, [""])[0].strip()


def _file_or_value(payload: dict[str, list[str]], file_key: str, value_key: str) -> str:
    return _value(payload, file_key) or _value(payload, value_key)


def _parse_payload(content_type: str, body: bytes, db_path: Path) -> dict[str, list[str]]:
    if not content_type.startswith("multipart/form-data"):
        return parse_qs(body.decode("utf-8"))

    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\nMIME-Version: 1.0\n\n".encode("utf-8") + body
    )
    payload: dict[str, list[str]] = {}
    upload_dir = _upload_dir(db_path)
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""
        if filename:
            if not content:
                continue
            upload_dir.mkdir(parents=True, exist_ok=True)
            destination = upload_dir / f"{uuid.uuid4().hex[:8]}-{_safe_filename(filename)}"
            destination.write_bytes(content)
            payload.setdefault(name, []).append(str(destination))
        else:
            payload.setdefault(name, []).append(content.decode(part.get_content_charset() or "utf-8").strip())
    return payload


def _upload_dir(db_path: Path) -> Path:
    root = db_path.parent if str(db_path.parent) != "." else Path(".")
    return root / ".goldset" / "uploads"


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return safe or "upload"


def _notice(value: str) -> str:
    if not value:
        return ""
    return f'<div class="notice">{_linkify_file_uris(value)}</div>'


def _linkify_file_uris(value: str) -> str:
    escaped = html.escape(value)
    return re.sub(
        r"(file:///[^\s<]+)",
        r'<a href="\1">\1</a>',
        escaped,
    )


def _status(value: int | None) -> str:
    if value is None:
        return "Pending"
    return "Pass" if value == 1 else "Fail"


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.3f}"


def _css() -> str:
    return """
:root { color-scheme: light; --ink: #17202a; --muted: #64748b; --line: #d8dee8; --panel: #ffffff; --bg: #f6f7f9; --accent: #0f766e; --accent-dark: #115e59; --warn: #b45309; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); display: grid; grid-template-columns: 248px 1fr; min-height: 100vh; }
aside { background: #16211f; color: white; padding: 26px 22px; position: sticky; top: 0; height: 100vh; overflow: auto; }
.brand { font-size: 22px; font-weight: 800; margin-bottom: 28px; }
nav { display: grid; gap: 6px; margin-bottom: 28px; }
nav a { color: #d9f4ee; text-decoration: none; padding: 9px 10px; border-radius: 7px; }
nav a:hover { background: rgba(255,255,255,.09); }
code { display: block; white-space: pre-wrap; word-break: break-word; font-size: 12px; color: inherit; background: rgba(255,255,255,.09); border-radius: 7px; padding: 9px; }
.path-label { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #9cc9c0; margin-bottom: 8px; }
main { padding: 30px; max-width: 1280px; width: 100%; }
.solo { max-width: 900px; margin: 0 auto; display: block; }
.hero { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 18px; }
.eyebrow { margin: 0 0 8px; color: var(--accent-dark); font-weight: 800; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }
h1 { margin: 0; max-width: 760px; font-size: 34px; line-height: 1.08; letter-spacing: 0; }
h2 { margin: 0 0 8px; font-size: 20px; letter-spacing: 0; }
h3 { margin: 18px 0 8px; font-size: 15px; letter-spacing: 0; }
.grid { display: grid; gap: 16px; }
.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin: 18px 0; }
.metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 1px 2px rgba(15,23,42,.04); }
.metric { padding: 16px; }
.metric span { color: var(--muted); font-size: 13px; }
.metric strong { display: block; font-size: 28px; margin-top: 5px; }
.panel { padding: 18px; margin-bottom: 16px; }
.notice { background: #e7f7f1; border: 1px solid #9bd8c6; color: #134e4a; border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; }
.error pre { white-space: pre-wrap; background: #fff7ed; border: 1px solid #fed7aa; padding: 14px; border-radius: 8px; }
.muted, .empty { color: var(--muted); }
.stack { display: grid; gap: 12px; }
.compact { margin-top: 12px; }
.row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
label { display: grid; gap: 6px; font-weight: 700; font-size: 13px; }
label.inline { display: flex; align-items: center; gap: 8px; }
input, select, textarea { width: 100%; border: 1px solid #cbd5e1; border-radius: 7px; padding: 9px 10px; font: inherit; background: white; color: var(--ink); }
textarea { min-height: 70px; resize: vertical; }
button { border: 0; border-radius: 7px; background: var(--accent); color: white; padding: 10px 13px; font-weight: 800; cursor: pointer; width: fit-content; }
button:hover { background: var(--accent-dark); }
button:disabled { background: #94a3b8; cursor: not-allowed; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { text-align: left; vertical-align: top; border-bottom: 1px solid #e5e7eb; padding: 10px 9px; font-size: 14px; }
th { color: #475569; background: #f8fafc; }
td span { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
tr.selected td { background: #f0fdfa; }
.review-card { border: 1px solid #dbe3ea; border-radius: 8px; padding: 14px; margin: 12px 0; background: #fbfdff; }
summary { cursor: pointer; font-weight: 750; color: var(--accent-dark); margin: 10px 0; }
pre { white-space: pre-wrap; word-break: break-word; background: #f1f5f9; border-radius: 7px; padding: 11px; font-size: 13px; }
.backtest { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px; }
@media (max-width: 920px) { body { grid-template-columns: 1fr; } aside { position: static; height: auto; } .two, .row, .metrics { grid-template-columns: 1fr; } .hero { display: block; } }
"""
