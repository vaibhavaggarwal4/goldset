from __future__ import annotations

import argparse
import importlib.util
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from evalkit.errors import UserFacingError
from evalkit.evaluators import EvaluationEngine
from evalkit.golden import load_golden_set, load_outcomes
from evalkit.loaders import load_cases_from_csv
from evalkit.mapping import import_data, import_outcomes, inspect_csv
from evalkit.metrics import calculate_calibration_metrics, calculate_outcome_correlations, calculate_reliability_metrics
from evalkit.providers.factory import make_provider
from evalkit.reports import render_html_report
from evalkit.review_ui import serve_review_ui
from evalkit.rubrics import load_rubric
from evalkit.self_improvement import create_eval_target, extract_review_signals, generate_findings
from evalkit.storage import EvalStore
from evalkit.workbench_ui import serve_workbench
from evalkit.workspace import SUPPORTED_SURFACES, create_workspace, suggest_dimensions


def main() -> None:
    try:
        _main()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130)
    except UserFacingError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        if "--debug" in sys.argv:
            raise
        print(f"\nUnexpected error: {exc}", file=sys.stderr)
        print("Run again with --debug before the command for a full traceback, for example: evalkit --debug run ...", file=sys.stderr)
        raise SystemExit(1)


def _main() -> None:
    parser = argparse.ArgumentParser(prog="evalkit", description="Run GTM and marketing evals.")
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks for unexpected errors.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check local setup and common configuration.")
    doctor_parser.add_argument("--check-openai", action="store_true", help="Also check OpenAI package and env vars.")
    doctor_parser.add_argument("--check-ollama", action="store_true", help="Also check local Ollama setup.")

    setup_parser = subparsers.add_parser("setup", help="Set up optional local providers.")
    setup_parser.add_argument("target", choices=["ollama"])
    setup_parser.add_argument("--model", default=os.getenv("EVALKIT_OLLAMA_MODEL") or "llama3.1")
    setup_parser.add_argument("--base-url", default=os.getenv("EVALKIT_OLLAMA_BASE_URL") or "http://127.0.0.1:11434")
    setup_parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts.")

    init_parser = subparsers.add_parser("init", help="Create an editable eval workspace from templates.")
    init_parser.add_argument("--surface", required=True, choices=sorted(SUPPORTED_SURFACES))
    init_parser.add_argument("--name", required=True)
    init_parser.add_argument("--output-dir", default=".")
    init_parser.add_argument("--force", action="store_true")

    suggest_parser = subparsers.add_parser("suggest-rubric", help="Suggest rubric dimensions for a marketing surface.")
    suggest_parser.add_argument("--surface", required=True, choices=sorted(SUPPORTED_SURFACES))

    inspect_parser = subparsers.add_parser("inspect-csv", help="Inspect a messy export and suggest likely columns.")
    inspect_parser.add_argument("--source", required=True)

    import_parser = subparsers.add_parser("import", help="Transform a campaign/content CSV into Goldset data.csv format.")
    import_parser.add_argument("--source", required=True)
    import_parser.add_argument("--mapping", required=True)
    import_parser.add_argument("--output", required=True)

    import_outcomes_parser = subparsers.add_parser("import-outcomes", help="Transform a results CSV into Goldset outcomes.csv format.")
    import_outcomes_parser.add_argument("--source", required=True)
    import_outcomes_parser.add_argument("--mapping", required=True)
    import_outcomes_parser.add_argument("--output", required=True)

    run_parser = subparsers.add_parser("run", help="Run an evaluation suite.")
    run_parser.add_argument("--rubric", required=True)
    run_parser.add_argument("--input", required=True)
    run_parser.add_argument("--db", default="evalkit.sqlite")
    run_parser.add_argument("--suite-name", default="Marketing Evaluation")
    run_parser.add_argument("--provider", default="heuristic", choices=["heuristic", "openai", "ollama"])
    run_parser.add_argument("--model")
    run_parser.add_argument("--report", help="HTML report path. Defaults to reports/<suite-name>-<run-id>.html.")

    report_parser = subparsers.add_parser("report", help="Render an HTML report for a run.")
    report_parser.add_argument("--db", default="evalkit.sqlite")
    report_parser.add_argument("--run-id", default="latest")
    report_parser.add_argument("--output", help="HTML report path. Defaults to reports/<suite-name>-<run-id>.html.")

    review_parser = subparsers.add_parser("review", help="Start the local human review UI.")
    review_parser.add_argument("--db", default="evalkit.sqlite")
    review_parser.add_argument("--run-id", default="latest")
    review_parser.add_argument("--host", default="127.0.0.1")
    review_parser.add_argument("--port", type=int, default=8765)

    ui_parser = subparsers.add_parser("ui", help="Start the local Goldset Workbench.")
    ui_parser.add_argument("--db", default="evalkit.sqlite")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8766)

    signals_parser = subparsers.add_parser("signals", help="Extract structured review signals from human reviews.")
    signals_parser.add_argument("--db", default="evalkit.sqlite")
    signals_parser.add_argument("--run-id", default="latest")

    findings_parser = subparsers.add_parser("findings", help="Group review signals into recurring failure findings.")
    findings_parser.add_argument("--db", default="evalkit.sqlite")
    findings_parser.add_argument("--run-id", default="latest")
    findings_parser.add_argument("--min-cases", type=int, default=1)

    targets_parser = subparsers.add_parser("targets", help="Create an eval target from a finding.")
    targets_parser.add_argument("--db", default="evalkit.sqlite")
    targets_parser.add_argument("--finding-id", type=int, required=True)
    targets_parser.add_argument("--owner", default="unassigned")
    targets_parser.add_argument("--output-dir", default="eval-targets")

    learn_parser = subparsers.add_parser("learn", help="Run the self-improvement loop in one friendly step.")
    learn_parser.add_argument("--db", default="evalkit.sqlite")
    learn_parser.add_argument("--run-id", default="latest")
    learn_parser.add_argument("--min-cases", type=int, default=1)
    learn_parser.add_argument("--owner", default="unassigned")
    learn_parser.add_argument("--output-dir", default="eval-targets")
    learn_parser.add_argument("--export-targets", action="store_true", help="Export target folders for all generated findings.")

    calibrate_parser = subparsers.add_parser("calibrate", help="Compare evaluator and human judgments against a golden set.")
    calibrate_parser.add_argument("--db", default="evalkit.sqlite")
    calibrate_parser.add_argument("--run-id", default="latest")
    calibrate_parser.add_argument("--golden-set", required=True)

    outcomes_parser = subparsers.add_parser("outcomes", help="Correlate eval pass/fail results with business outcome metrics.")
    outcomes_parser.add_argument("--db", default="evalkit.sqlite")
    outcomes_parser.add_argument("--run-id", default="latest")
    outcomes_parser.add_argument("--outcomes", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="Run an eval on historical data and compare against labels/outcomes.")
    backtest_parser.add_argument("--rubric", required=True)
    backtest_parser.add_argument("--input", required=True)
    backtest_parser.add_argument("--golden-set", required=True)
    backtest_parser.add_argument("--outcomes")
    backtest_parser.add_argument("--db", default="evalkit.sqlite")
    backtest_parser.add_argument("--suite-name", default="Backtest")
    backtest_parser.add_argument("--provider", default="heuristic", choices=["heuristic", "openai", "ollama"])
    backtest_parser.add_argument("--model")
    backtest_parser.add_argument("--report", help="HTML report path. Defaults to reports/<suite-name>-<run-id>.html.")

    args = parser.parse_args()
    _dispatch(args)


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "doctor":
        doctor(args)
    elif args.command == "setup":
        setup(args)
    elif args.command == "init":
        init_workspace(args)
    elif args.command == "suggest-rubric":
        suggest_rubric(args)
    elif args.command == "inspect-csv":
        inspect_csv_command(args)
    elif args.command == "import":
        import_command(args)
    elif args.command == "import-outcomes":
        import_outcomes_command(args)
    elif args.command == "run":
        run(args)
    elif args.command == "report":
        report(args)
    elif args.command == "review":
        review(args)
    elif args.command == "ui":
        ui(args)
    elif args.command == "signals":
        signals(args)
    elif args.command == "findings":
        findings(args)
    elif args.command == "targets":
        targets(args)
    elif args.command == "learn":
        learn(args)
    elif args.command == "calibrate":
        calibrate(args)
    elif args.command == "outcomes":
        outcomes(args)
    elif args.command == "backtest":
        backtest(args)


def _print_html_report(report_path: str | Path) -> None:
    resolved = Path(report_path).resolve()
    print(f"HTML report: {resolved}")
    print(f"Open report: {resolved.as_uri()}")


def run(args: argparse.Namespace) -> None:
    rubric = load_rubric(args.rubric)
    cases = load_cases_from_csv(args.input, artifact_type=rubric.artifact_type)
    provider = make_provider(args.provider)
    engine = EvaluationEngine(provider=provider, model=args.model)
    store = EvalStore(args.db)
    run_id = store.create_run(
        suite_name=args.suite_name,
        rubric=rubric,
        provider=provider.name,
        model=args.model,
        input_path=args.input,
    )
    results = engine.evaluate_cases(cases, rubric)
    store.save_results(run_id, results)
    report_path = render_html_report(store, run_id, args.report)
    print(f"Run complete: {run_id}")
    print(f"Cases evaluated: {len(cases)}")
    _print_html_report(report_path)
    print(f"Next: open the report in a browser, or run evalkit review --db {args.db} --run-id latest")


def report(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    report_path = render_html_report(store, run_id, args.output)
    _print_html_report(report_path)


def review(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    serve_review_ui(store, run_id, args.host, args.port)


def ui(args: argparse.Namespace) -> None:
    serve_workbench(args.db, args.host, args.port)


def setup(args: argparse.Namespace) -> None:
    if args.target == "ollama":
        setup_ollama(args)


def setup_ollama(args: argparse.Namespace) -> None:
    model = args.model
    base_url = args.base_url.rstrip("/")
    print(f"Ollama setup target: model={model}, base_url={base_url}")
    if not args.yes:
        print("This may install Ollama, start the local server, and download a model.")
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            raise UserFacingError("Ollama setup cancelled.")

    ollama_path = shutil.which("ollama")
    if not ollama_path:
        print("Ollama CLI: missing")
        if platform.system() == "Darwin" and shutil.which("brew"):
            print("Installing Ollama with Homebrew...")
            _run_command(["brew", "install", "ollama"], timeout=1200)
            ollama_path = shutil.which("ollama")
        else:
            raise UserFacingError(
                "Ollama is not installed and automatic install is only supported on macOS with Homebrew.\n"
                "Install Ollama from https://ollama.com, then rerun evalkit setup ollama --model "
                f"{model}."
            )
    else:
        print(f"Ollama CLI: {ollama_path}")

    if not ollama_path:
        raise UserFacingError("Ollama installed, but the ollama command was not found on PATH. Restart your terminal and try again.")

    if not _ollama_server_reachable(base_url):
        print("Starting Ollama server...")
        subprocess.Popen([ollama_path, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if not _wait_for_ollama(base_url, attempts=30):
            raise UserFacingError(f"Ollama server did not become reachable at {base_url}. Try running ollama serve in another terminal.")
    print(f"Ollama server: reachable at {base_url}")

    print(f"Pulling model: {model}")
    _run_command([ollama_path, "pull", model], timeout=1800)
    print("\nOllama setup complete.")
    print(f"Next: choose provider Ollama and model {model} in the Workbench, or run evalkit run --provider ollama --model {model} ...")


def _run_command(command: list[str], *, timeout: int) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError as exc:
        raise UserFacingError(f"Command not found: {command[0]}") from exc
    output: list[str] = []
    started = time.time()
    assert process.stdout is not None
    while True:
        line = process.stdout.readline()
        if line:
            output.append(line)
            print(line, end="")
        if process.poll() is not None:
            break
        if time.time() - started > timeout:
            process.kill()
            raise UserFacingError(f"Command timed out: {' '.join(command)}")
    if process.returncode != 0:
        detail = "".join(output[-40:]).strip() or "No command output."
        raise UserFacingError(f"Command failed: {' '.join(command)}\n{detail}")


def _ollama_server_reachable(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError):
        return False


def _wait_for_ollama(base_url: str, *, attempts: int) -> bool:
    for _ in range(attempts):
        if _ollama_server_reachable(base_url):
            return True
        time.sleep(0.75)
    return False


def signals(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    signal_ids = extract_review_signals(store, run_id)
    print(f"Review signals extracted: {len(signal_ids)}")
    if not signal_ids:
        print("No actionable signals yet. Submit human reviews with failures, corrections, disagreements, or rubric issues.")
        return
    for row in store.review_signal_rows(run_id):
        print(
            f"{row['id']}: {row['dimension_name']} / {row['case_id']} / "
            f"{row['signal_type']} / {row['failure_reason'] or 'unspecified'}"
        )
    print("Next: evalkit findings --db " f"{args.db} --run-id {run_id}")


def findings(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    finding_ids = generate_findings(store, run_id, min_cases=args.min_cases)
    print(f"Findings generated: {len(finding_ids)}")
    for row in store.finding_rows(run_id):
        print(f"{row['id']}: {row['title']} [cases={row['case_count']}, status={row['status']}]")
    print("Next: evalkit targets --db " f"{args.db} --finding-id FINDING_ID")


def targets(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    target_id, export_path = create_eval_target(
        store=store,
        finding_id=args.finding_id,
        owner=args.owner,
        output_dir=args.output_dir,
    )
    print(f"Eval target created: {target_id}")
    if export_path:
        print(f"Target folder: {Path(export_path).resolve()}")


def learn(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    signal_ids = extract_review_signals(store, run_id)
    print(f"Step 1/3: Found {len(signal_ids)} actionable review signal(s).")
    if not signal_ids:
        print("No learning loop yet. Add human reviews with failures, corrections, disagreements, or rubric issues.")
        print(f"Next: evalkit review --db {args.db} --run-id {run_id}")
        return

    finding_ids = generate_findings(store, run_id, min_cases=args.min_cases)
    print(f"Step 2/3: Grouped signals into {len(finding_ids)} finding(s).")
    for row in store.finding_rows(run_id):
        print(f"- Finding {row['id']}: {row['title']}")

    if args.export_targets:
        print("Step 3/3: Exporting eval target folders.")
        for finding_id in finding_ids:
            target_id, export_path = create_eval_target(
                store=store,
                finding_id=finding_id,
                owner=args.owner,
                output_dir=args.output_dir,
            )
            print(f"- Target {target_id}: {Path(export_path).resolve() if export_path else 'saved'}")
    else:
        first_finding = finding_ids[0]
        print("Step 3/3: Findings are ready.")
        print(
            "Next: create a focused improvement task with "
            f"evalkit targets --db {args.db} --finding-id {first_finding} --owner \"{args.owner}\""
        )
        print("Tip: add --export-targets to evalkit learn to create target folders automatically.")
    print(f"Next: refresh your report with evalkit report --db {args.db} --run-id {run_id}")


def calibrate(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    store.run(run_id)
    labels = load_golden_set(args.golden_set)
    reliability = calculate_reliability_metrics(store.dimension_rows(run_id), labels)
    calibration = calculate_calibration_metrics(store.dimension_rows(run_id), store.human_review_rows(run_id), labels)
    _print_reliability(reliability)
    _print_calibration(calibration)


def outcomes(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    store.run(run_id)
    outcome_rows = load_outcomes(args.outcomes)
    correlations = calculate_outcome_correlations(store.case_rows(run_id), store.dimension_rows(run_id), outcome_rows)
    _print_outcome_correlations(correlations)


def backtest(args: argparse.Namespace) -> None:
    rubric = load_rubric(args.rubric)
    cases = load_cases_from_csv(args.input, artifact_type=rubric.artifact_type)
    provider = make_provider(args.provider)
    engine = EvaluationEngine(provider=provider, model=args.model)
    store = EvalStore(args.db)
    run_id = store.create_run(
        suite_name=args.suite_name,
        rubric=rubric,
        provider=provider.name,
        model=args.model,
        input_path=args.input,
    )
    results = engine.evaluate_cases(cases, rubric)
    store.save_results(run_id, results)
    report_path = render_html_report(store, run_id, args.report)
    print(f"Backtest run complete: {run_id}")
    print(f"Cases evaluated: {len(cases)}")
    _print_html_report(report_path)
    labels = load_golden_set(args.golden_set)
    reliability = calculate_reliability_metrics(store.dimension_rows(run_id), labels)
    _print_reliability(reliability)
    if args.outcomes:
        outcome_rows = load_outcomes(args.outcomes)
        correlations = calculate_outcome_correlations(store.case_rows(run_id), store.dimension_rows(run_id), outcome_rows)
        _print_outcome_correlations(correlations)


def init_workspace(args: argparse.Namespace) -> None:
    files = create_workspace(
        surface=args.surface,
        name=args.name,
        output_dir=args.output_dir,
        force=args.force,
    )
    print(f"Created eval workspace: {files.root.resolve()}")
    print(f"- Rubric: {files.rubric}")
    print(f"- Data CSV: {files.data}")
    print(f"- Golden set: {files.golden_set}")
    print(f"- Outcomes: {files.outcomes}")
    print(f"- Guide: {files.readme}")
    print("\nSuggested dimensions:")
    for name, evaluator, description in suggest_dimensions(args.surface):
        print(f"- {name} [{evaluator}]: {description}")
    print(f"\nNext: edit {files.rubric}, then run evalkit run --rubric {files.rubric} --input {files.data}")


def suggest_rubric(args: argparse.Namespace) -> None:
    print(f"Suggested rubric dimensions for {args.surface}:")
    for name, evaluator, description in suggest_dimensions(args.surface):
        print(f"- {name} [{evaluator}]: {description}")
    print("\nTip: run evalkit init --surface " f"{args.surface} --name my_eval to create editable files.")


def inspect_csv_command(args: argparse.Namespace) -> None:
    inspection = inspect_csv(args.source)
    print(f"CSV: {inspection.path}")
    print(f"Rows: {inspection.row_count}")
    print(f"Columns: {len(inspection.columns)}")
    print("\nAll columns:")
    for column in inspection.columns:
        print(f"- {column}")
    _print_column_group("Likely ID columns", inspection.likely_id_columns)
    _print_column_group("Likely content columns", inspection.likely_content_columns)
    _print_column_group("Likely outcome columns", inspection.likely_outcome_columns)
    print("\nNext: copy a template from templates/mappings/ and edit it to match these columns.")


def import_command(args: argparse.Namespace) -> None:
    output = import_data(args.source, args.mapping, args.output)
    print(f"Imported campaign data: {Path(output).resolve()}")
    print(f"Next: evalkit run --rubric RUBRIC.yaml --input {output}")


def import_outcomes_command(args: argparse.Namespace) -> None:
    output = import_outcomes(args.source, args.mapping, args.output)
    print(f"Imported outcome data: {Path(output).resolve()}")
    print(f"Next: evalkit outcomes --db evalkit.sqlite --run-id latest --outcomes {output}")


def _print_column_group(label: str, columns: list[str]) -> None:
    print(f"\n{label}:")
    if not columns:
        print("- none detected")
        return
    for column in columns:
        print(f"- {column}")


def _print_reliability(metrics: dict) -> None:
    print("\nEvaluator Reliability")
    print(f"Matched golden labels: {metrics['matched_labels']}/{metrics['total_golden_labels']}")
    if metrics["matched_labels"] < metrics["total_golden_labels"]:
        print("Note: unmatched labels usually belong to dimensions with no machine result, such as human_review-only dimensions.")
    _print_classification("Overall", metrics["overall"])
    if metrics["by_dimension"]:
        print("\nBy dimension")
        for dimension_name, values in metrics["by_dimension"].items():
            _print_classification(f"- {dimension_name}", values)


def _print_calibration(metrics: dict) -> None:
    print("\nCalibration")
    print(f"Human-machine agreement: {_fmt_pct(metrics['human_machine_agreement'])}")
    print(f"Human-human pairwise agreement: {_fmt_pct(metrics['human_human_pairwise_agreement'])}")
    _print_classification("Human vs golden", metrics["human_vs_golden"])
    if metrics["reviewer_vs_golden"]:
        print("\nReviewer vs golden")
        for reviewer, values in metrics["reviewer_vs_golden"].items():
            _print_classification(f"- {reviewer}", values)


def _print_outcome_correlations(correlations: dict) -> None:
    print("\nBusiness Outcome Correlation")
    print("Note: r=N/A means there were too few rows or no pass/fail variance for that metric.")
    print("Overall pass")
    for metric_name, values in correlations["overall_pass"].items():
        print(f"- {metric_name}: r={_fmt_num(values['pearson'])}, n={values['n']}")
    if correlations["by_dimension"]:
        print("\nBy dimension")
        for dimension_name, metric_values in correlations["by_dimension"].items():
            rendered = ", ".join(
                f"{metric}=r {_fmt_num(values['pearson'])} (n={values['n']})"
                for metric, values in metric_values.items()
            )
            print(f"- {dimension_name}: {rendered}")


def _print_classification(label: str, values: dict) -> None:
    print(
        f"{label}: total={values['total']}, accuracy={_fmt_pct(values['accuracy'])}, "
        f"precision={_fmt_pct(values['precision'])}, recall={_fmt_pct(values['recall'])}, "
        f"FPR={_fmt_pct(values['false_positive_rate'])}, FNR={_fmt_pct(values['false_negative_rate'])}, "
        f"FP={values['false_positives']}, FN={values['false_negatives']}"
    )


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.3f}"


def doctor(args: argparse.Namespace) -> None:
    openai_installed = importlib.util.find_spec("openai") is not None
    checks = [
        ("Python version", sys.version_info >= (3, 10), f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        ("PyYAML installed", importlib.util.find_spec("yaml") is not None, "required for rubric files"),
        ("Example rubric exists", Path("examples/lifecycle_email/rubric.yaml").exists(), "examples/lifecycle_email/rubric.yaml"),
        ("Example CSV exists", Path("examples/lifecycle_email/sample.csv").exists(), "examples/lifecycle_email/sample.csv"),
    ]
    if args.check_openai:
        checks.extend(
            [
                ("OpenAI package installed", openai_installed, "available" if openai_installed else 'install with python -m pip install -e ".[openai]"'),
                ("OPENAI_API_KEY set", bool(os.getenv("OPENAI_API_KEY")), "export OPENAI_API_KEY='your_key'"),
                ("OpenAI model configured", bool(os.getenv("EVALKIT_OPENAI_MODEL")), "export EVALKIT_OPENAI_MODEL='MODEL_NAME' or pass --model"),
            ]
        )
    if args.check_ollama:
        ollama_installed = shutil.which("ollama") is not None
        checks.extend(
            [
                ("Ollama CLI installed", ollama_installed, "available" if ollama_installed else "install from https://ollama.com"),
                ("Ollama model configured", bool(os.getenv("EVALKIT_OLLAMA_MODEL")), "export EVALKIT_OLLAMA_MODEL='llama3.1' or pass --model"),
                ("Ollama base URL", True, os.getenv("EVALKIT_OLLAMA_BASE_URL") or "http://127.0.0.1:11434"),
            ]
        )

    failed = False
    for label, ok, detail in checks:
        mark = "ok" if ok else "missing"
        print(f"{mark:7} {label}: {detail}")
        failed = failed or not ok

    if failed:
        sys.stdout.flush()
        raise UserFacingError(
            "Setup check found one or more issues.\n"
            "Fix the missing items above, then run evalkit doctor again."
        )

    print("\nSetup looks good. Try: evalkit run --rubric examples/lifecycle_email/rubric.yaml --input examples/lifecycle_email/sample.csv --provider heuristic")


if __name__ == "__main__":
    main()
