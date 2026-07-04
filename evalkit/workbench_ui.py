from __future__ import annotations

from email import policy
from email.parser import BytesParser
import html
import json
import os
import re
import shutil
import socketserver
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.error
import urllib.request

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
            query = parse_qs(parsed.query)
            if parsed.path == "/samples":
                try:
                    self._send_text(_sample_text(_value(query, "path")))
                except UserFacingError as exc:
                    self._send_text(str(exc))
                return
            if parsed.path != "/":
                self.send_error(404)
                return
            self._send_html(_render_home(db_path, query))

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("content-length", "0"))
                payload = _parse_payload(self.headers.get("content-type", ""), self.rfile.read(length), db_path)
                if self.path == "/actions/run":
                    notice, run_id = _run_eval(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "results", "notice": notice})
                elif self.path == "/actions/backtest":
                    notice, run_id = _run_backtest(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "backtest", "notice": notice})
                elif self.path == "/actions/report":
                    notice, run_id = _render_report(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": _value(payload, "step") or "results", "notice": notice})
                elif self.path == "/actions/review":
                    notice, run_id = _save_review(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "review", "notice": notice})
                elif self.path == "/actions/learn":
                    notice, run_id = _learn(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "learn", "notice": notice})
                elif self.path == "/actions/calibrate":
                    notice, run_id = _calibrate(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "calibrate", "notice": notice})
                elif self.path == "/actions/outcomes":
                    notice, run_id = _outcomes(db_path, payload)
                    self._redirect("/", {"run_id": run_id, "step": "calibrate", "notice": notice})
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

        def _send_text(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
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
    rubric_path = _required_file(payload, "rubric_file", "rubric YAML")
    input_path = _required_file(payload, "input_file", "input CSV")
    suite_name = _value(payload, "suite_name") or "Marketing Evaluation"
    category = _value(payload, "category") or "Lifecycle"
    provider_name = _value(payload, "provider") or "heuristic"
    model = _value(payload, "model") or None
    openai_api_key = _value(payload, "openai_api_key") or None
    ollama_base_url = _value(payload, "ollama_base_url") or None
    report_path = _value(payload, "report_path") or None

    rubric = load_rubric(rubric_path)
    cases = load_cases_from_csv(input_path, artifact_type=rubric.artifact_type)
    provider = make_provider(provider_name, openai_api_key=openai_api_key, ollama_base_url=ollama_base_url)
    engine = EvaluationEngine(provider=provider, model=model)
    store = EvalStore(db_path)
    run_id = store.create_run(
        suite_name=suite_name,
        rubric=rubric,
        provider=provider.name,
        model=model,
        input_path=input_path,
        category=category,
    )
    store.save_results(run_id, engine.evaluate_cases(cases, rubric))
    report = render_html_report(store, run_id, report_path)
    return f"Run complete. Evaluated {len(cases)} case(s). Report: {report.resolve().as_uri()}", run_id


def _ollama_server_reachable(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError):
        return False


def _ollama_model_available(base_url: str, model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False
    models = payload.get("models") or []
    names = {row.get("name", "").split(":", 1)[0] for row in models}
    names.update(row.get("name", "") for row in models)
    return model in names


def _ollama_status_html() -> str:
    model = os.getenv("EVALKIT_OLLAMA_MODEL") or "llama3.1"
    base_url = os.getenv("EVALKIT_OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
    installed = shutil.which("ollama") is not None
    server = _ollama_server_reachable(base_url) if installed else False
    model_ready = _ollama_model_available(base_url, model) if server else False
    rows = [
        ("Ollama installed", installed, "Install with evalkit setup ollama --model MODEL_NAME."),
        ("Local server reachable", server, "Run evalkit setup ollama --model MODEL_NAME to start it."),
        (f"Default model ready ({model})", model_ready, "Pull it with evalkit setup ollama --model MODEL_NAME."),
    ]
    rendered = "".join(
        f"""<li class="status-row {"ok" if ok else "missing"}">
  <span>{_icon("check" if ok else "guide")}</span>
  <strong>{html.escape(label)}</strong>
  <small>{html.escape("Ready" if ok else fix)}</small>
</li>"""
        for label, ok, fix in rows
    )
    return f'<ul class="status-list">{rendered}</ul>'


def _run_backtest(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    notice, run_id = _run_eval(db_path, payload)
    golden_path = _required_file(payload, "golden_file", "golden set CSV")
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
    if not signal_ids:
        return (
            "No review signals yet. Save at least one human review with a failure, correction, disagreement, or rubric issue, then run Learn again.",
            run_id,
        )
    try:
        finding_ids = generate_findings(store, run_id, min_cases=1)
    except UserFacingError as exc:
        return str(exc).replace("\n", " "), run_id
    return f"Learning loop updated. Signals: {len(signal_ids)}. Findings: {len(finding_ids)}.", run_id


def _calibrate(db_path: Path, payload: dict[str, list[str]]) -> tuple[str, str]:
    store = EvalStore(db_path)
    run_id = _run_id(store, payload)
    labels = load_golden_set(_required_file(payload, "golden_file", "golden set CSV"))
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
        load_outcomes(_required_file(payload, "outcomes_file", "outcomes CSV")),
    )
    metric_parts = [
        f"{name}: r={_num(values['pearson'])}, n={values['n']}"
        for name, values in correlations["overall_pass"].items()
    ]
    return f"Outcome correlation complete. Overall pass: {'; '.join(metric_parts) or 'no numeric outcome metrics found'}.", run_id


def _render_home(db_path: Path, query: dict[str, list[str]]) -> str:
    store = EvalStore(db_path)
    all_runs = _run_rows(store)
    selected_category = _value(query, "category")
    runs = _filter_runs(all_runs, selected_category)
    raw_step = _value(query, "step")
    run_id = None if raw_step in {"setup", "guide"} and not _value(query, "run_id") else _selected_run_id(store, runs, query)
    step = _active_step(query, run_id)
    notice = _value(query, "notice")
    selected = store.run(run_id) if run_id else None
    case_rows = store.case_rows(run_id) if run_id else []
    dimension_rows = store.dimension_rows(run_id) if run_id else []
    human_rows = store.human_review_rows(run_id) if run_id else []
    metrics = calculate_metrics(case_rows, dimension_rows, human_rows) if run_id else None
    findings = store.finding_rows(run_id) if run_id else []
    review_rows = _review_queue(case_rows, dimension_rows, human_rows) if run_id else []
    failures = [row for row in dimension_rows if row["passed"] == 0][:12]
    content = _step_content(step, selected, metrics, failures, run_id, review_rows, findings)
    stepper_html = "" if step == "guide" else _stepper(step, run_id)
    metrics_html = "" if step == "guide" else f'<section class="metrics">{_metric_cards(metrics, len(human_rows), len(findings))}</section>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Goldset Workbench</title>
  <style>{_css()}</style>
  <script>{_js()}</script>
</head>
<body>
  <aside>
    <div class="brand"><span class="brand-mark">G</span><span><strong>Goldset</strong><small>Marketing evals</small></span></div>
    <a class="new-workflow" href="/?step=setup">{_icon("plus")}<span>New workflow</span></a>
    {_guide_nav(step)}
    {_category_nav(all_runs, selected_category)}
    {_workflow_nav(runs, run_id)}
    <div class="path-label">Database</div>
    <code>{html.escape(str(db_path.resolve()))}</code>
  </aside>
  <main>
    <section class="hero">
      <div>
        <p class="eyebrow">Guided eval workflow</p>
        <h1>{_hero_title(step, selected)}</h1>
        <p class="hero-copy">{_hero_copy(step)}</p>
      </div>
      <div class="hero-actions">
        <form method="post" action="/actions/report">
          <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
          <input type="hidden" name="step" value="{html.escape(step)}">
          <button {"disabled" if not run_id else ""} data-progress="Generating report...">{_icon("report")}<span>Generate report</span></button>
        </form>
      </div>
    </section>
    {_notice(notice)}
    {stepper_html}
    {metrics_html}
    {content}
  </main>
</body>
</html>"""


def _render_error(db_path: Path, message: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Goldset Workbench Error</title><style>{_css()}</style></head>
<body><main class="solo"><section class="panel error"><h1>Something needs attention</h1><pre>{html.escape(message)}</pre><a href="/">Back to workbench</a><p class="muted">Database: {html.escape(str(db_path.resolve()))}</p></section></main></body>
</html>"""


def _active_step(query: dict[str, list[str]], run_id: str | None) -> str:
    step = _value(query, "step")
    allowed = {"guide", "setup", "results", "review", "learn", "calibrate", "backtest"}
    if step in allowed:
        return step
    return "results" if run_id else "setup"


def _step_content(
    step: str,
    selected,
    metrics: dict | None,
    failures,
    run_id: str | None,
    review_rows: list[tuple],
    findings,
) -> str:
    if step == "guide":
        return f"""<section class="panel focus">{_guide_panel()}</section>"""
    if step == "setup":
        return f"""<section class="panel focus">{_run_form()}</section>"""
    if step == "results":
        return f"""<section class="panel focus">{_results_panel(selected, metrics, failures)}</section>"""
    if step == "review":
        return f"""<section class="panel focus">{_review_panel(run_id, review_rows)}</section>"""
    if step == "learn":
        return f"""<section class="panel focus">{_learn_panel(run_id, findings)}</section>"""
    if step == "calibrate":
        return f"""<section class="panel focus">{_calibrate_panel(run_id)}</section>"""
    if step == "backtest":
        return f"""<section class="panel focus">{_backtest_panel(run_id)}</section>"""
    return f"""<section class="panel focus">{_run_form()}</section>"""


def _hero_title(step: str, selected) -> str:
    if step == "guide":
        return "How Goldset works."
    if step == "setup":
        return "Start a new evaluation workflow."
    if selected:
        return html.escape(selected["suite_name"])
    return "Evaluate, review, and improve AI-generated marketing work."


def _hero_copy(step: str) -> str:
    copy = {
        "guide": "Use Goldset as a learning loop: define the standard, run evals, review judgment calls, learn from repeated feedback, then calibrate and backtest before scaling.",
        "setup": "",
        "results": "Inspect what passed, what failed, and which dimensions need human judgment before you move on.",
        "review": "Turn expert judgment into structured feedback the system can learn from.",
        "learn": "Group review signals into recurring findings and decide what should improve next.",
        "calibrate": "Use a golden set and outcome data to test whether the evaluator is trustworthy. Treat this as a preflight before relying on a rubric or judge.",
        "backtest": "Run historical examples as a separate version so you can compare evaluator behavior before using it on new campaign work.",
    }
    return copy.get(step, "")


def _stepper(active_step: str, run_id: str | None) -> str:
    steps = [
        ("setup", "Setup", "file"),
        ("results", "Results", "chart"),
        ("review", "Review", "review"),
        ("learn", "Learn", "learn"),
        ("calibrate", "Calibrate", "target"),
        ("backtest", "Backtest", "history"),
    ]
    links = []
    for step, label, icon in steps:
        disabled = step != "setup" and not run_id
        class_name = "active" if step == active_step else ""
        content = f'{_icon(icon)}<span>{label}</span>'
        if disabled:
            links.append(f'<span class="step disabled">{content}</span>')
        else:
            links.append(f'<a class="step {class_name}" href="{_step_url(step, None if step == "setup" else run_id)}">{content}</a>')
    return f'<nav class="stepper">{"".join(links)}</nav>'


def _guide_nav(step: str) -> str:
    selected = "selected" if step == "guide" else ""
    return f"""<div class="rail-section guide-section">
  <a class="guide-link {selected}" href="/?step=guide">{_icon("guide")}<span>How to use Goldset</span></a>
</div>"""


def _category_nav(runs, selected_category: str) -> str:
    counts: dict[str, int] = {}
    for row in runs:
        category = row["category"] or "General"
        counts[category] = counts.get(category, 0) + 1
    if not counts:
        return f'<div class="rail-section"><h2>{_icon("folder")}<span>Categories</span></h2><p class="rail-empty">No workflows yet.</p></div>'
    all_class = "selected" if not selected_category else ""
    all_item = f'<a class="{all_class}" href="/"><span>All</span><strong>{len(runs)}</strong></a>'
    items = "\n".join(
        f'<a class="{"selected" if category == selected_category else ""}" href="/?category={urlencode({"": category})[1:]}"><span>{html.escape(category)}</span><strong>{count}</strong></a>'
        for category, count in sorted(counts.items())
    )
    return f'<div class="rail-section"><h2>{_icon("folder")}<span>Categories</span></h2><div class="category-list">{all_item}{items}</div></div>'


def _workflow_nav(runs, selected_run_id: str | None) -> str:
    if not runs:
        return f'<div class="rail-section"><h2>{_icon("history")}<span>Previous workflows</span></h2><p class="rail-empty">Run your first eval to create history.</p></div>'
    rows = "\n".join(
        f"""<a class="workflow-link {"selected" if row['id'] == selected_run_id else ""}" href="{_step_url("results", row['id'])}">
  <span>{html.escape(row['suite_name'])}</span>
  <small>{html.escape(row['category'] or 'General')} · {html.escape(row['created_at'][:10])}</small>
</a>"""
        for row in runs
    )
    return f'<div class="rail-section"><h2>{_icon("history")}<span>Previous workflows</span></h2><div class="workflow-list">{rows}</div></div>'


def _step_url(step: str, run_id: str | None) -> str:
    params = {"step": step}
    if run_id:
        params["run_id"] = run_id
    return f"/?{urlencode(params)}"


def _filter_runs(runs, category: str):
    if not category:
        return runs
    return [row for row in runs if (row["category"] or "General") == category]


def _run_form() -> str:
    return f"""<h2>Create workflow</h2>
<p class="muted">Choose a rubric YAML and an input CSV to evaluate marketing outputs.</p>
<form method="post" action="/actions/run" class="stack" enctype="multipart/form-data">
  <div class="row">
    <label>Category{_help("Use categories to group related workflows by channel, campaign, or experiment.")}
      <select name="category" data-category-select>
        <option value="Lifecycle">Lifecycle</option>
        <option value="Paid Social">Paid Social</option>
        <option value="Landing Page">Landing Page</option>
        <option value="SEO / Content">SEO / Content</option>
        <option value="Outbound">Outbound</option>
        <option value="Experiment">Experiment</option>
        <option value="General">General</option>
      </select>
    </label>
    <label>Workflow name{_help("Name this run like a version: Lifecycle v1, Paid Social hook test, Landing page model comparison.")}
      <input name="suite_name" data-workflow-name value="Lifecycle Email Evaluation">
    </label>
  </div>
  <div class="row">
    <label>Rubric YAML file{_help("Upload a YAML rubric with dimensions, evaluators, and pass/fail criteria.")}
      <input name="rubric_file" type="file" accept=".yaml,.yml" required>
      {_sample_link("examples/lifecycle_email/rubric.yaml", "View sample rubric")}
    </label>
    <label>Input CSV file{_help("Upload the marketing outputs you want evaluated. Include case_id plus content fields such as subject_line, body, headline, or output.")}
      <input name="input_file" type="file" accept=".csv,text/csv" required>
      {_sample_link("examples/lifecycle_email/sample.csv", "View sample CSV")}
    </label>
  </div>
  {_provider_controls()}
  <button data-progress="Running eval...">{_icon("play")}<span>Run eval</span></button>
</form>"""


def _provider_controls() -> str:
    has_openai_model = bool(os.getenv("EVALKIT_OPENAI_MODEL"))
    has_ollama_model = bool(os.getenv("EVALKIT_OLLAMA_MODEL"))
    default_openai_model = os.getenv("EVALKIT_OPENAI_MODEL") or "gpt-5.5"
    openai_key_state = "OPENAI_API_KEY is set in this environment." if os.getenv("OPENAI_API_KEY") else "No OPENAI_API_KEY detected."
    openai_model_state = (
        f"EVALKIT_OPENAI_MODEL is set to {html.escape(os.getenv('EVALKIT_OPENAI_MODEL', ''))}."
        if has_openai_model
        else f"Default model is {default_openai_model}; you can change it below."
    )
    ollama_model_state = (
        f"EVALKIT_OLLAMA_MODEL is set to {html.escape(os.getenv('EVALKIT_OLLAMA_MODEL', ''))}."
        if has_ollama_model
        else "Enter a pulled local model below or set EVALKIT_OLLAMA_MODEL."
    )
    return f"""<div class="row provider-row">
    <label>Provider{_help("Choose heuristic for offline runs, OpenAI for hosted model judging, or Ollama for local open-source models.")}
      <select name="provider" data-provider-select>
        <option value="heuristic">Heuristic - offline</option>
        <option value="openai">OpenAI - hosted LLM judge</option>
        <option value="ollama">Ollama - local model</option>
      </select>
    </label>
    <label>Model{_help("Required for OpenAI and Ollama unless you set the matching environment variable. OpenAI defaults to gpt-5.5.")}
      <input name="model" data-model-input data-default-openai-model="{html.escape(default_openai_model)}" data-openai-model-configured="{str(has_openai_model).lower()}" data-ollama-model-configured="{str(has_ollama_model).lower()}" placeholder="optional for heuristic">
    </label>
  </div>
  <div class="provider-setup" data-provider-panel="heuristic">
    <strong>{_icon("check")} Offline mode</strong>
    <p>Runs without API keys or model downloads. Use this for setup checks and sample data; use OpenAI or Ollama when you want LLM judgment.</p>
  </div>
  <div class="provider-setup" data-provider-panel="openai" hidden>
    <strong>{_icon("guide")} OpenAI setup</strong>
    <p>{html.escape(openai_key_state)} {openai_model_state}</p>
    <label>OpenAI API key{_help("Used only for this local run. It is not saved to SQLite. You can leave this blank if OPENAI_API_KEY is set.")}
      <input name="openai_api_key" type="password" autocomplete="off" placeholder="sk-...">
    </label>
    <p class="setup-command">CLI alternative: <code>export OPENAI_API_KEY='your_key'</code> and <code>export EVALKIT_OPENAI_MODEL='{html.escape(default_openai_model)}'</code></p>
  </div>
  <div class="provider-setup" data-provider-panel="ollama" hidden>
    <strong>{_icon("guide")} Ollama setup</strong>
    <p>{ollama_model_state} Use the CLI for install, server startup, and model downloads so long-running setup has proper terminal progress.</p>
    {_ollama_status_html()}
    <div class="command-copy">
      <code data-ollama-command>evalkit setup ollama --model llama3.1</code>
      <a class="secondary-link" href="/?step=setup">{_icon("history")}<span>Check again</span></a>
    </div>
    <label>Ollama base URL{_help("Leave this alone unless your local Ollama server is running somewhere else.")}
      <input name="ollama_base_url" placeholder="http://127.0.0.1:11434">
    </label>
  </div>"""


def _guide_panel() -> str:
    return f"""<div class="guide-intro">
  <h2>Goldset workflow</h2>
  <p class="muted">Goldset helps teams turn marketing taste into a repeatable evaluation system. Start lightweight, add golden examples when trust matters, and use every review cycle to improve the rubric, prompt, model route, or workflow.</p>
</div>
<div class="guide-grid">
  <article class="guide-card">
    <span class="guide-step">1</span>
    <h3>Define the standard</h3>
    <p>Create or edit a rubric YAML. Good dimensions are specific, observable, and clear about whether they should be checked by code, an LLM judge, or a human.</p>
  </article>
  <article class="guide-card">
    <span class="guide-step">2</span>
    <h3>Upload examples</h3>
    <p>Add a CSV of marketing outputs with a stable case_id. Lifecycle emails, paid ads, landing pages, SEO briefs, and outbound copy can each use their own workflow category.</p>
  </article>
  <article class="guide-card">
    <span class="guide-step">3</span>
    <h3>Run the eval</h3>
    <p>Goldset applies deterministic checks first and routes qualitative dimensions to the selected evaluator. The results show pass rates and the dimensions that need attention.</p>
  </article>
  <article class="guide-card">
    <span class="guide-step">4</span>
    <h3>Review judgment calls</h3>
    <p>Use human review for subjective or risky dimensions. Reviewers choose pass/fail, a structured score, whether the rubric needs refinement, and the reason behind the judgment.</p>
  </article>
  <article class="guide-card">
    <span class="guide-step">5</span>
    <h3>Learn from patterns</h3>
    <p>Extract review signals and group them into recurring findings. Each finding becomes a concrete improvement target for the prompt, rubric, model choice, or workflow.</p>
  </article>
  <article class="guide-card">
    <span class="guide-step">6</span>
    <h3>Calibrate and backtest</h3>
    <p>Compare evaluator decisions against a golden set, then backtest on historical examples and outcome data before trusting a new rubric or judge at scale.</p>
  </article>
</div>
<div class="guide-two">
  <section class="guide-note">
    <h3>Where golden sets fit</h3>
    <p>A golden set is best created before you rely on a rubric or judge for real campaigns. If you do not have one yet, you can still run evals, collect human reviews, and promote your clearest reviewed examples into a golden set later.</p>
    <p>For calibration, run known examples first, compare machine judgment to trusted labels, refine the rubric or judge, then use the system on new marketing work.</p>
  </section>
  <section class="guide-note">
    <h3>File checklist</h3>
    <ul class="guide-list">
      <li><strong>Rubric YAML:</strong> the quality dimensions and evaluator routes.</li>
      <li><strong>Input CSV:</strong> the marketing outputs to evaluate.</li>
      <li><strong>Golden set CSV:</strong> trusted labels for calibration, optional but recommended.</li>
      <li><strong>Outcomes CSV:</strong> campaign or business metrics for backtesting.</li>
    </ul>
  </section>
</div>
<div class="next-actions">
  <a class="button-link" href="/?step=setup">{_icon("plus")}<span>Start new workflow</span></a>
  {_sample_link("examples/lifecycle_email/rubric.yaml", "View sample rubric")}
  {_sample_link("examples/lifecycle_email/sample.csv", "View sample CSV")}
</div>"""


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
        return f'<h2>Results</h2><p class="empty">Start with setup to create a run.</p><p><a class="button-link" href="{_step_url("setup", None)}">{_icon("plus")}<span>Start setup</span></a></p>'
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
<table><thead><tr><th>Case</th><th>Dimension</th><th>Rationale</th></tr></thead><tbody>{failure_rows}</tbody></table>
<div class="next-actions"><a class="button-link" href="{_step_url("review", selected['id'])}">{_icon("review")}<span>Start human review</span></a><a class="secondary-link" href="{_step_url("calibrate", selected['id'])}">{_icon("target")}<span>Calibrate evaluator</span></a></div>"""


def _review_panel(run_id: str | None, review_rows: list[tuple]) -> str:
    if not run_id:
        return f'<h2>Human review</h2><p class="empty">Run an eval to create a review queue.</p><p><a class="button-link" href="{_step_url("setup", None)}">{_icon("plus")}<span>Start setup</span></a></p>'
    cards = "\n".join(_review_card(run_id, case, dimension) for case, dimension in review_rows[:8])
    if not cards:
        cards = f'<p class="empty">No dimensions currently require human review.</p><p><a class="button-link" href="{_step_url("learn", run_id)}">{_icon("learn")}<span>Extract findings</span></a></p>'
    return f"""<h2>Human review queue {_info("Review is where a human accepts or rejects machine judgments, adds corrections, and flags rubric problems. These reviews become learning signals.")}</h2>
<p class="muted">Review the cases where human judgment matters most. Saved reviews disappear from the queue so progress is visible.</p>
{cards}"""


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
      <label>Optional quality rating{_help("Optional. Use this when pass/fail is not enough and you want a rough quality trend for reviewer calibration.")}
        <select name="score">
          <option value="">No score</option>
          <option value="1">1 - unusable</option>
          <option value="2">2 - major issues</option>
          <option value="3">3 - needs edits</option>
          <option value="4">4 - minor edits</option>
          <option value="5">5 - ready to ship</option>
        </select>
      </label>
    </div>
    <label>Failure reason<input name="failure_reason" placeholder="unclear_cta, off_brand, judge_wrong"></label>
    <label>Correction<textarea name="correction"></textarea></label>
    <label>Notes<textarea name="notes"></textarea></label>
    <div class="review-flag">
      <label class="inline"><input name="rubric_issue" type="checkbox"><span>Rubric needs refinement</span>{_info("Turn this on when the rubric itself is unclear, incomplete, too strict, too lenient, or asks the evaluator to judge the wrong thing.")}</label>
      <p>Use this when the issue is the standard, not just the output. Add the suggested rubric change in notes.</p>
    </div>
    <button data-progress="Saving review...">{_icon("check")}<span>Save review</span></button>
  </form>
</article>"""


def _learn_panel(run_id: str | None, findings) -> str:
    if not run_id:
        return f'<h2>Learning loop</h2><p class="empty">Run and review an eval before extracting findings.</p><p><a class="button-link" href="{_step_url("setup", None)}">{_icon("plus")}<span>Start setup</span></a></p>'
    button = f"""<form method="post" action="/actions/learn"><input type="hidden" name="run_id" value="{html.escape(run_id)}"><button data-progress="Extracting findings...">{_icon("learn")}<span>Extract signals and findings</span></button></form>"""
    rows = "\n".join(
        f"<tr><td>{row['id']}</td><td>{html.escape(row['title'])}</td><td>{html.escape(row['dimension_name'])}</td><td>{row['case_count']}</td></tr>"
        for row in findings
    )
    if not rows:
        rows = '<tr><td colspan="4" class="empty">No findings yet. Save a review with a failure, correction, disagreement, or rubric issue, then extract signals and findings.</td></tr>'
    return f"""<h2>Learning loop {_info("Learn converts human reviews into structured signals and recurring findings. Use it after review to decide what to improve next.")}</h2>
<p class="muted">Turn review feedback into recurring findings that can guide prompt, rubric, workflow, or model improvements.</p>
{button}
<table><thead><tr><th>ID</th><th>Finding</th><th>Dimension</th><th>Cases</th></tr></thead><tbody>{rows}</tbody></table>
<div class="next-actions"><a class="button-link" href="{_step_url("calibrate", run_id)}">{_icon("target")}<span>Calibrate evaluator</span></a><a class="secondary-link" href="{_step_url("backtest", run_id)}">{_icon("history")}<span>Run backtest</span></a></div>"""


def _calibrate_panel(run_id: str | None) -> str:
    disabled = "disabled" if not run_id else ""
    return f"""<h2>Calibrate evaluator {_info("Calibration compares evaluator decisions against expert-labeled golden examples so you can estimate trust before using the setup on new work.")}</h2>
<p class="muted">Best practice: create a golden set before trusting a new rubric, model, or prompt route. Calibration still needs an eval run, so use it as a preflight check on known examples, then run new campaign work.</p>
<div class="grid two">
  <form method="post" action="/actions/calibrate" class="stack" enctype="multipart/form-data">
    <h3>Golden set calibration</h3>
    <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
    <label>Golden set CSV file{_help("Upload expert labels for known examples. Goldset compares evaluator decisions against these labels.")}
      <input name="golden_file" type="file" accept=".csv,text/csv" required>
      {_sample_link("examples/golden_sets/lifecycle_email_golden_set.csv", "View sample golden set")}
    </label>
    <button {disabled} data-progress="Running calibration...">{_icon("target")}<span>Run calibration</span></button>
  </form>
  <form method="post" action="/actions/outcomes" class="stack" enctype="multipart/form-data">
    <h3>Outcome correlation</h3>
    <input type="hidden" name="run_id" value="{html.escape(run_id or '')}">
    <label>Outcomes CSV file{_help("Upload business metrics by case_id, such as CTR, conversion_rate, reply_rate, or activation_rate.")}
      <input name="outcomes_file" type="file" accept=".csv,text/csv" required>
      {_sample_link("examples/outcomes/lifecycle_email_outcomes.csv", "View sample outcomes")}
    </label>
    <button {disabled} data-progress="Calculating correlation...">{_icon("chart")}<span>Calculate correlation</span></button>
  </form>
</div>
<div class="next-actions"><a class="button-link" href="{_step_url("backtest", run_id)}">{_icon("history")}<span>Run historical backtest</span></a></div>"""


def _backtest_panel(run_id: str | None) -> str:
    return f"""<h2>Backtest a new version {_info("Backtesting runs a rubric or model route on historical examples so you can compare versions before changing live workflows.")}</h2>
<p class="muted">Use this before trusting a new evaluator setup. Run known historical examples, compare against a golden set, then decide whether the rubric or judge is ready for new work.</p>
<form method="post" action="/actions/backtest" class="stack backtest" enctype="multipart/form-data">
  <h3>Run historical backtest</h3>
  <div class="row">
    <label>Rubric YAML file{_help("Upload the rubric version you want to test against historical examples.")}
      <input name="rubric_file" type="file" accept=".yaml,.yml" required>
      {_sample_link("examples/lifecycle_email/rubric.yaml", "View sample rubric")}
    </label>
    <label>Input CSV file{_help("Upload historical examples with the same case_id values used in your golden set.")}
      <input name="input_file" type="file" accept=".csv,text/csv" required>
      {_sample_link("examples/lifecycle_email/sample.csv", "View sample CSV")}
    </label>
  </div>
  <div class="row">
    <label>Golden set file{_help("Upload expert labels so the backtest can report reliability.")}
      <input name="golden_file" type="file" accept=".csv,text/csv" required>
      {_sample_link("examples/golden_sets/lifecycle_email_golden_set.csv", "View sample golden set")}
    </label>
  </div>
  <div class="row">
    <label>Suite name<input name="suite_name" value="Lifecycle Email Backtest"></label>
    <label>Category
      <select name="category">
        <option value="Lifecycle">Lifecycle</option>
        <option value="Paid Social">Paid Social</option>
        <option value="Landing Page">Landing Page</option>
        <option value="SEO / Content">SEO / Content</option>
        <option value="Outbound">Outbound</option>
        <option value="Experiment">Experiment</option>
        <option value="General">General</option>
      </select>
    </label>
  </div>
  {_provider_controls()}
  <button data-progress="Running backtest...">{_icon("history")}<span>Run backtest</span></button>
</form>"""


def _metric_cards(metrics: dict | None, review_count: int, finding_count: int) -> str:
    if not metrics:
        values = [("Cases", "0", "file"), ("Pass rate", "N/A", "chart"), ("Human reviews", "0", "review"), ("Findings", "0", "learn")]
    else:
        values = [
            ("Cases", str(metrics["total_cases"]), "file"),
            ("Pass rate", _pct(metrics["pass_rate"]), "chart"),
            ("Human reviews", str(review_count), "review"),
            ("Findings", str(finding_count), "learn"),
        ]
    return "".join(f'<div class="metric"><span class="metric-icon">{_icon(icon)}</span><span>{label}</span><strong>{value}</strong></div>' for label, value, icon in values)


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


def _required_file(payload: dict[str, list[str]], file_key: str, label: str) -> str:
    path = _value(payload, file_key)
    if not path:
        raise UserFacingError(f"Choose a {label} file, then try again.")
    return path


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


def _help(text: str) -> str:
    return f'<span class="help" tabindex="0" aria-label="{html.escape(text)}">?</span>'


def _info(text: str) -> str:
    return f'<span class="info" tabindex="0" aria-label="{html.escape(text)}">i</span>'


def _sample_link(path: str, label: str) -> str:
    url = f"/samples?{urlencode({'path': path})}"
    return f'<a class="sample-link" href="{html.escape(url)}" data-sample-link data-sample-label="{html.escape(label)}">{_icon("file")}<span>{html.escape(label)}</span></a>'


def _sample_text(path: str) -> str:
    if not path:
        raise UserFacingError("Sample path is missing.")
    root = Path.cwd().resolve()
    sample_path = (root / path).resolve()
    try:
        sample_path.relative_to(root)
    except ValueError as exc:
        raise UserFacingError("Sample path must stay inside this repo.") from exc
    if not sample_path.exists() or not sample_path.is_file():
        raise UserFacingError(f"Sample file not found: {path}")
    return sample_path.read_text(encoding="utf-8")


def _icon(name: str) -> str:
    paths = {
        "plus": '<path d="M12 5v14M5 12h14"/>',
        "file": '<path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z"/><path d="M14 2v5h5"/>',
        "chart": '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 16v-5"/><path d="M12 16V8"/><path d="M16 16v-8"/>',
        "review": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/><path d="m8 11 2 2 5-5"/>',
        "learn": '<path d="M12 3v18"/><path d="M5 7a4 4 0 0 1 7-2 4 4 0 0 1 7 2v8a4 4 0 0 1-7 2 4 4 0 0 1-7-2z"/>',
        "target": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
        "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v6h6"/><path d="M12 7v5l3 2"/>',
        "folder": '<path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
        "report": '<path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/><path d="M9 13h6M9 17h6M9 9h2"/>',
        "play": '<path d="m8 5 11 7-11 7z"/>',
        "check": '<path d="m5 12 4 4L19 6"/>',
        "guide": '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
        "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    }
    return (
        '<svg class="icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f'{paths.get(name, paths["file"])}</svg>'
    )


def _js() -> str:
    return """
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-sample-link]').forEach((link) => {
    link.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      await showSamplePreview(link.href, link.dataset.sampleLabel || link.textContent.trim());
    });
  });
  document.querySelectorAll('form').forEach((form) => {
    const category = form.querySelector('[data-category-select]');
    const workflowName = form.querySelector('[data-workflow-name]');
    if (category && workflowName) {
      const defaults = {
        'Lifecycle': 'Lifecycle Email Evaluation',
        'Paid Social': 'Paid Social Ad Evaluation',
        'Landing Page': 'Landing Page Copy Evaluation',
        'SEO / Content': 'SEO Content Brief Evaluation',
        'Outbound': 'Outbound Copy Evaluation',
        'Experiment': 'Marketing Experiment Evaluation',
        'General': 'Marketing Evaluation'
      };
      workflowName.addEventListener('input', () => {
        workflowName.dataset.userEdited = 'true';
      });
      category.addEventListener('change', () => {
        if (workflowName.dataset.userEdited === 'true') return;
        workflowName.value = defaults[category.value] || defaults.General;
      });
    }
    const select = form.querySelector('[data-provider-select]');
    if (select) {
      const model = form.querySelector('[data-model-input]');
      const panels = form.querySelectorAll('[data-provider-panel]');
      const ollamaCommand = form.querySelector('[data-ollama-command]');
      let modelTouched = false;
      const updateOllamaCommand = () => {
        if (!ollamaCommand || !model) return;
        const modelName = model.value.trim() || 'llama3.1';
        ollamaCommand.textContent = `evalkit setup ollama --model ${modelName}`;
      };
      if (model) {
        model.addEventListener('input', () => {
          modelTouched = true;
          updateOllamaCommand();
        });
      }
      const updateProvider = () => {
        const provider = select.value;
        panels.forEach((panel) => {
          panel.hidden = panel.dataset.providerPanel !== provider;
        });
        if (model) {
          const placeholders = {
            heuristic: 'optional for heuristic',
            openai: model.dataset.defaultOpenaiModel || 'gpt-5.5',
            ollama: 'llama3.1'
          };
          model.placeholder = placeholders[provider] || 'optional';
          if (provider === 'openai' && !modelTouched && !model.value.trim()) {
            model.value = model.dataset.defaultOpenaiModel || 'gpt-5.5';
          }
          if (provider === 'ollama' && !modelTouched && !model.value.trim()) {
            model.value = 'llama3.1';
          }
          model.required = (provider === 'openai' && model.dataset.openaiModelConfigured !== 'true') ||
            (provider === 'ollama' && model.dataset.ollamaModelConfigured !== 'true');
        }
        updateOllamaCommand();
      };
      select.addEventListener('change', updateProvider);
      updateProvider();
    }
    form.addEventListener('submit', (event) => {
      const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      const confirmation = submitter?.dataset.confirm;
      if (confirmation && !window.confirm(confirmation)) {
        event.preventDefault();
        return;
      }
      const label = submitter?.dataset.progress || 'Working...';
      showProgress(label, form);
      form.querySelectorAll('button').forEach((button) => {
        button.disabled = true;
      });
      if (submitter) {
        submitter.classList.add('is-loading');
        const span = submitter.querySelector('span');
        if (span) span.textContent = label;
      }
    });
  });
});

function showProgress(label, form) {
  let overlay = document.querySelector('.progress-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'progress-overlay';
    overlay.innerHTML = `
      <div class="progress-card">
        <div class="spinner" aria-hidden="true"></div>
        <strong data-progress-title></strong>
        <p data-progress-copy></p>
        <ol data-progress-steps></ol>
      </div>
    `;
    document.body.appendChild(overlay);
  }
  const provider = form?.querySelector('[data-provider-select]')?.value || '';
  const model = form?.querySelector('[data-model-input]')?.value || '';
  const providerCopy = provider === 'openai'
    ? `Calling OpenAI${model ? ` with ${model}` : ''}. This can take a moment depending on quota, latency, and number of judged dimensions.`
    : provider === 'ollama'
      ? `Calling your local Ollama model${model ? ` (${model})` : ''}. Larger local models can take longer.`
      : 'Running offline checks and saving results locally.';
  overlay.querySelector('[data-progress-title]').textContent = label;
  overlay.querySelector('[data-progress-copy]').textContent = providerCopy;
  overlay.querySelector('[data-progress-steps]').innerHTML = progressSteps(label).map((step, index) => (
    `<li class="${index === 0 ? 'active' : ''}"><span>${index + 1}</span>${step}</li>`
  )).join('');
  overlay.hidden = false;
  let index = 0;
  window.clearInterval(window.__goldsetProgressTimer);
  window.__goldsetProgressTimer = window.setInterval(() => {
    const steps = [...overlay.querySelectorAll('[data-progress-steps] li')];
    if (!steps.length) return;
    steps.forEach((step, stepIndex) => {
      step.classList.toggle('done', stepIndex < index);
      step.classList.toggle('active', stepIndex === index);
    });
    index = Math.min(index + 1, steps.length - 1);
  }, 1400);
}

function progressSteps(label) {
  if (label.includes('eval') || label.includes('backtest')) {
    return ['Reading rubric and CSV', 'Running deterministic checks', 'Judging qualitative dimensions', 'Saving results and rendering report'];
  }
  if (label.includes('calibration')) {
    return ['Reading golden set', 'Matching labels to results', 'Calculating reliability metrics'];
  }
  if (label.includes('correlation')) {
    return ['Reading outcomes file', 'Joining cases to metrics', 'Calculating correlations'];
  }
  if (label.includes('findings')) {
    return ['Reading human reviews', 'Extracting review signals', 'Grouping recurring findings'];
  }
  return ['Preparing request', 'Saving changes', 'Refreshing workbench'];
}

async function showSamplePreview(url, label) {
  const modal = sampleModal();
  modal.querySelector('[data-sample-title]').textContent = label;
  const body = modal.querySelector('[data-sample-body]');
  body.textContent = 'Loading sample...';
  modal.hidden = false;
  try {
    const response = await fetch(url);
    body.textContent = await response.text();
  } catch (error) {
    body.textContent = `Could not load sample: ${error}`;
  }
}

function sampleModal() {
  let modal = document.querySelector('.sample-modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.className = 'sample-modal';
  modal.hidden = true;
  modal.innerHTML = `
    <div class="sample-card" role="dialog" aria-modal="true" aria-labelledby="sample-title">
      <div class="sample-header">
        <strong id="sample-title" data-sample-title>Sample</strong>
        <button type="button" data-sample-close>Close</button>
      </div>
      <pre data-sample-body></pre>
    </div>
  `;
  modal.addEventListener('click', (event) => {
    if (event.target === modal) modal.hidden = true;
  });
  modal.querySelector('[data-sample-close]').addEventListener('click', () => {
    modal.hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') modal.hidden = true;
  });
  document.body.appendChild(modal);
  return modal;
}
"""


def _css() -> str:
    return """
:root { color-scheme: light; --ink: #151922; --muted: #667085; --line: #d9e0ea; --line-strong: #c5cedb; --panel: #ffffff; --bg: #f4f6f8; --rail: #141a22; --rail-soft: #202936; --accent: #0d766d; --accent-dark: #0a5f59; --blue: #2563eb; --amber: #b7791f; --purple: #6d5bd0; --danger: #b42318; --shadow: 0 16px 40px rgba(21,25,34,.08); --shadow-soft: 0 4px 14px rgba(21,25,34,.06); }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }
.icon { width: 17px; height: 17px; flex: 0 0 auto; }
aside { background: var(--rail); color: white; padding: 24px 18px; position: sticky; top: 0; height: 100vh; overflow: auto; border-right: 1px solid #0f141b; }
.brand { display: flex; align-items: center; gap: 11px; margin-bottom: 24px; }
.brand-mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 9px; background: #f7c948; color: #171717; font-weight: 900; box-shadow: 0 8px 20px rgba(247,201,72,.22); }
.brand strong { display: block; font-size: 20px; line-height: 1; }
.brand small { display: block; color: #aeb8c6; margin-top: 3px; font-size: 12px; }
.new-workflow { display: flex; align-items: center; gap: 8px; background: #e7f7f1; color: #134e4a; text-decoration: none; border-radius: 8px; padding: 10px 12px; font-weight: 850; margin-bottom: 22px; box-shadow: 0 8px 22px rgba(13,118,109,.12); }
.rail-section { border-top: 1px solid rgba(255,255,255,.12); padding-top: 16px; margin-top: 16px; }
.guide-section { margin-top: 0; padding-top: 0; border-top: 0; }
.guide-link { display: flex; align-items: center; gap: 8px; color: #e6edf5; text-decoration: none; border-radius: 8px; padding: 10px; border: 1px solid transparent; font-weight: 800; }
.guide-link:hover, .guide-link.selected { background: var(--rail-soft); border-color: rgba(255,255,255,.08); }
.guide-link .icon { color: #f7c948; }
.rail-section h2 { display: flex; align-items: center; gap: 7px; color: #aeb8c6; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 9px; }
.rail-section h2 .icon { width: 14px; height: 14px; }
.rail-empty { color: #aeb8c6; font-size: 13px; margin: 0; line-height: 1.45; }
.category-list, .workflow-list { display: grid; gap: 6px; }
.category-list a, .workflow-link { color: #e6edf5; text-decoration: none; border-radius: 8px; padding: 9px 10px; border: 1px solid transparent; }
.category-list a { display: flex; justify-content: space-between; align-items: center; }
.category-list a:hover, .workflow-link:hover, .category-list a.selected, .workflow-link.selected { background: var(--rail-soft); border-color: rgba(255,255,255,.08); }
.category-list strong { color: #aeb8c6; font-size: 12px; }
.workflow-link { display: grid; gap: 3px; }
.workflow-link span { font-weight: 800; font-size: 14px; }
.workflow-link small { color: #aeb8c6; font-size: 12px; }
code { display: block; white-space: pre-wrap; word-break: break-word; font-size: 12px; color: #d9e2ec; background: rgba(255,255,255,.08); border-radius: 8px; padding: 10px; border: 1px solid rgba(255,255,255,.07); }
.path-label { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #aeb8c6; margin: 18px 0 8px; }
main { padding: 34px; max-width: 1320px; width: 100%; }
.solo { max-width: 900px; margin: 0 auto; display: block; }
.hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 18px; }
.eyebrow { margin: 0 0 8px; color: var(--accent-dark); font-weight: 800; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }
h1 { margin: 0; max-width: 820px; font-size: 36px; line-height: 1.08; letter-spacing: 0; }
.hero-copy { max-width: 780px; margin: 10px 0 0; color: var(--muted); font-size: 16px; line-height: 1.55; }
.hero-copy:empty { display: none; }
h2 { margin: 0 0 8px; font-size: 21px; letter-spacing: 0; }
h3 { margin: 18px 0 8px; font-size: 15px; letter-spacing: 0; }
.grid { display: grid; gap: 16px; }
.two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin: 18px 0; }
.metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow-soft); }
.metric { padding: 16px; position: relative; overflow: hidden; }
.metric > span:not(.metric-icon) { color: var(--muted); font-size: 13px; font-weight: 750; }
.metric-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--accent); background: #e7f7f1; margin-bottom: 10px; }
.metric strong { display: block; font-size: 28px; margin-top: 5px; letter-spacing: 0; }
.panel { padding: 18px; margin-bottom: 16px; }
.focus { padding: 24px; box-shadow: var(--shadow); }
.notice { background: #e7f7f1; border: 1px solid #9bd8c6; color: #134e4a; border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; font-weight: 650; }
.stepper { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; margin: 18px 0; }
.step { display: flex; align-items: center; justify-content: center; gap: 7px; text-align: center; text-decoration: none; border: 1px solid var(--line); border-radius: 8px; background: white; color: #344054; padding: 10px 8px; font-weight: 850; font-size: 13px; box-shadow: 0 1px 0 rgba(21,25,34,.03); }
.step .icon { width: 15px; height: 15px; }
.step.active { border-color: #83c7bc; background: #e7f7f1; color: #134e4a; box-shadow: inset 0 0 0 1px rgba(13,118,109,.12); }
.step.disabled { color: #94a3b8; background: #eef2f7; }
.error pre { white-space: pre-wrap; background: #fff7ed; border: 1px solid #fed7aa; padding: 14px; border-radius: 8px; }
.error h1 { font-size: 32px; }
.muted, .empty { color: var(--muted); line-height: 1.5; }
.stack { display: grid; gap: 12px; }
.compact { margin-top: 12px; }
.row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
label { display: grid; gap: 6px; font-weight: 700; font-size: 13px; }
label.inline { display: flex; align-items: center; gap: 8px; }
.help { display: inline-grid; place-items: center; width: 18px; height: 18px; margin-left: 5px; border-radius: 999px; background: #eef6f4; color: var(--accent-dark); border: 1px solid #bdddd7; font-size: 12px; font-weight: 900; cursor: help; position: relative; }
.help:hover::after, .help:focus::after { content: attr(aria-label); position: absolute; z-index: 10; left: 22px; top: 50%; transform: translateY(-50%); width: 260px; padding: 9px 10px; border-radius: 8px; background: #111827; color: white; font-size: 12px; font-weight: 650; line-height: 1.35; box-shadow: var(--shadow); }
.info { display: inline-grid; place-items: center; width: 18px; height: 18px; margin-left: 6px; border-radius: 999px; background: #eef2ff; color: #4338ca; border: 1px solid #c7d2fe; font-size: 12px; font-weight: 900; cursor: help; position: relative; vertical-align: middle; }
.info:hover::after, .info:focus::after { content: attr(aria-label); position: absolute; z-index: 10; left: 22px; top: 50%; transform: translateY(-50%); width: 280px; padding: 9px 10px; border-radius: 8px; background: #111827; color: white; font-size: 12px; font-weight: 650; line-height: 1.35; box-shadow: var(--shadow); text-transform: none; letter-spacing: 0; }
.sample-link { display: inline-flex; align-items: center; gap: 6px; width: fit-content; color: var(--accent-dark); text-decoration: none; font-size: 12px; font-weight: 800; }
.sample-link:hover { text-decoration: underline; }
.sample-link .icon { width: 14px; height: 14px; }
.sample-modal { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: 24px; background: rgba(20,26,34,.48); backdrop-filter: blur(2px); }
.sample-card { width: min(860px, 100%); max-height: min(760px, calc(100vh - 48px)); display: grid; grid-template-rows: auto minmax(0, 1fr); background: white; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 24px 70px rgba(21,25,34,.24); overflow: hidden; }
.sample-header { display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 14px 16px; border-bottom: 1px solid var(--line); }
.sample-header strong { font-size: 15px; }
.sample-header button { padding: 8px 11px; background: #eef2f7; color: #334155; box-shadow: none; }
.sample-card pre { margin: 0; height: 100%; overflow: auto; border-radius: 0; background: #f8fafc; border: 0; padding: 16px; }
.provider-row { align-items: start; }
.provider-setup { border: 1px solid #dbe3ea; background: #fbfdff; border-radius: 8px; padding: 14px; display: grid; gap: 10px; }
.provider-setup strong { display: flex; align-items: center; gap: 8px; color: #1f2937; }
.provider-setup p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.setup-command code { display: inline; padding: 2px 5px; white-space: normal; color: #334155; background: #eef2f7; }
.status-list { list-style: none; display: grid; gap: 8px; padding: 0; margin: 0; }
.status-row { display: grid; grid-template-columns: 24px minmax(0, .8fr) minmax(0, 1.2fr); gap: 8px; align-items: center; border: 1px solid #e5e7eb; border-radius: 8px; padding: 9px 10px; background: white; }
.status-row span { display: grid; place-items: center; color: var(--muted); }
.status-row.ok span { color: var(--accent); }
.status-row.missing span { color: var(--amber); }
.status-row strong { display: block; font-size: 13px; }
.status-row small { color: var(--muted); font-size: 12px; line-height: 1.35; }
.command-copy { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.command-copy code { color: #334155; background: #eef2f7; }
.command-list { display: grid; gap: 7px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.command-list code { color: #334155; background: #eef2f7; }
input, select, textarea { width: 100%; border: 1px solid #cbd5e1; border-radius: 8px; padding: 10px 11px; font: inherit; background: white; color: var(--ink); transition: border-color .15s ease, box-shadow .15s ease; }
input:focus, select:focus, textarea:focus { outline: none; border-color: #83c7bc; box-shadow: 0 0 0 4px rgba(13,118,109,.12); }
input[type="file"] { padding: 9px; background: #f8fafc; border-style: dashed; }
input[type="checkbox"] { width: 16px; height: 16px; accent-color: var(--accent); }
textarea { min-height: 70px; resize: vertical; }
button { display: inline-flex; align-items: center; gap: 8px; border: 0; border-radius: 8px; background: var(--accent); color: white; padding: 10px 13px; font-weight: 850; cursor: pointer; width: fit-content; box-shadow: 0 8px 18px rgba(13,118,109,.18); }
button:hover { background: var(--accent-dark); }
button:disabled { background: #94a3b8; cursor: not-allowed; }
button.is-loading { background: #0a5f59; }
.progress-overlay { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; padding: 24px; background: rgba(20,26,34,.42); backdrop-filter: blur(2px); }
.progress-card { width: min(460px, 100%); background: white; border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 24px 70px rgba(21,25,34,.24); padding: 22px; text-align: left; }
.progress-card strong { display: block; margin-top: 12px; font-size: 18px; }
.progress-card p { margin: 7px 0 14px; color: var(--muted); font-size: 13px; line-height: 1.45; }
.spinner { width: 34px; height: 34px; margin: 0 auto; border-radius: 999px; border: 4px solid #dbe8e5; border-top-color: var(--accent); animation: spin .8s linear infinite; }
.progress-card ol { list-style: none; display: grid; gap: 8px; padding: 0; margin: 0; }
.progress-card li { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
.progress-card li span { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 999px; background: #eef2f7; color: #475569; font-weight: 900; font-size: 12px; }
.progress-card li.active { color: var(--ink); font-weight: 800; }
.progress-card li.active span { background: #e7f7f1; color: var(--accent-dark); }
.progress-card li.done span { background: var(--accent); color: white; }
@keyframes spin { to { transform: rotate(360deg); } }
.next-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 16px; }
.button-link, .secondary-link { display: inline-flex; align-items: center; gap: 8px; border-radius: 8px; padding: 10px 13px; font-weight: 850; text-decoration: none; }
.button-link { background: var(--accent); color: white; box-shadow: 0 8px 18px rgba(13,118,109,.18); }
.secondary-link { border: 1px solid var(--line); color: var(--accent-dark); background: white; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { text-align: left; vertical-align: top; border-bottom: 1px solid #e5e7eb; padding: 11px 10px; font-size: 14px; }
th { color: #475569; background: #f8fafc; font-weight: 850; }
td span { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
tr.selected td { background: #f0fdfa; }
.review-card { border: 1px solid #dbe3ea; border-radius: 8px; padding: 16px; margin: 12px 0; background: #fbfdff; box-shadow: 0 1px 0 rgba(21,25,34,.03); }
.review-flag { border: 1px solid #e4d7fb; background: #fbf8ff; border-radius: 8px; padding: 12px; }
.review-flag .inline { justify-content: flex-start; font-weight: 850; color: #42307d; }
.review-flag p { margin: 7px 0 0 24px; color: var(--muted); font-size: 13px; line-height: 1.45; }
summary { cursor: pointer; font-weight: 750; color: var(--accent-dark); margin: 10px 0; }
pre { white-space: pre-wrap; word-break: break-word; background: #f1f5f9; border-radius: 7px; padding: 11px; font-size: 13px; }
.backtest { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 16px; }
.guide-intro { max-width: 860px; }
.guide-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-top: 18px; }
.guide-card { border: 1px solid #dbe3ea; border-radius: 8px; padding: 16px; background: #fbfdff; }
.guide-card h3 { margin-top: 10px; font-size: 16px; }
.guide-card p, .guide-note p, .guide-list { color: var(--muted); font-size: 14px; line-height: 1.55; }
.guide-step { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; background: #e7f7f1; color: var(--accent-dark); font-weight: 900; }
.guide-two { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, .9fr); gap: 14px; margin-top: 16px; }
.guide-note { border-top: 1px solid var(--line); padding-top: 14px; }
.guide-list { margin: 8px 0 0; padding-left: 18px; }
.guide-list li { margin-bottom: 7px; }
@media (max-width: 1100px) { .guide-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 920px) { body { grid-template-columns: 1fr; } aside { position: static; height: auto; } .two, .row, .metrics, .stepper, .guide-grid, .guide-two, .command-list, .status-row { grid-template-columns: 1fr; } .hero { display: block; } }
"""
