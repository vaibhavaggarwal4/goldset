from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

from evalkit.errors import UserFacingError
from evalkit.evaluators import EvaluationEngine
from evalkit.loaders import load_cases_from_csv
from evalkit.providers.factory import make_provider
from evalkit.reports import render_html_report
from evalkit.review_ui import serve_review_ui
from evalkit.rubrics import load_rubric
from evalkit.self_improvement import create_eval_target, extract_review_signals, generate_findings
from evalkit.storage import EvalStore


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

    run_parser = subparsers.add_parser("run", help="Run an evaluation suite.")
    run_parser.add_argument("--rubric", required=True)
    run_parser.add_argument("--input", required=True)
    run_parser.add_argument("--db", default="evalkit.sqlite")
    run_parser.add_argument("--suite-name", default="Marketing Evaluation")
    run_parser.add_argument("--provider", default="heuristic", choices=["heuristic", "openai"])
    run_parser.add_argument("--model")
    run_parser.add_argument("--report", default="report.html")

    report_parser = subparsers.add_parser("report", help="Render an HTML report for a run.")
    report_parser.add_argument("--db", default="evalkit.sqlite")
    report_parser.add_argument("--run-id", default="latest")
    report_parser.add_argument("--output", default="report.html")

    review_parser = subparsers.add_parser("review", help="Start the local human review UI.")
    review_parser.add_argument("--db", default="evalkit.sqlite")
    review_parser.add_argument("--run-id", default="latest")
    review_parser.add_argument("--host", default="127.0.0.1")
    review_parser.add_argument("--port", type=int, default=8765)

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

    args = parser.parse_args()
    _dispatch(args)


def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "doctor":
        doctor(args)
    elif args.command == "run":
        run(args)
    elif args.command == "report":
        report(args)
    elif args.command == "review":
        review(args)
    elif args.command == "signals":
        signals(args)
    elif args.command == "findings":
        findings(args)
    elif args.command == "targets":
        targets(args)


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
    print(f"HTML report: {Path(report_path).resolve()}")
    print(f"Next: open the report in a browser, or run evalkit review --db {args.db} --run-id latest")


def report(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    report_path = render_html_report(store, run_id, args.output)
    print(f"HTML report: {Path(report_path).resolve()}")


def review(args: argparse.Namespace) -> None:
    store = EvalStore(args.db)
    run_id = store.latest_run_id() if args.run_id == "latest" else args.run_id
    serve_review_ui(store, run_id, args.host, args.port)


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

    print("\nSetup looks good. Try: evalkit run --rubric examples/lifecycle_email/rubric.yaml --input examples/lifecycle_email/sample.csv --provider heuristic --report lifecycle-report.html")


if __name__ == "__main__":
    main()
