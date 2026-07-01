# Architecture

Goldset is the working project name. The implementation uses the neutral package name `evalkit` so the codebase can be renamed without changing core classes.

## Components

- `Rubric`: a versioned quality standard for a marketing surface.
- `RubricDimension`: one measurable quality dimension.
- `EvalCase`: one input, generated artifact, and metadata bundle.
- `EvaluationEngine`: applies a rubric to cases.
- `LogicEvaluator`: deterministic checks where everyone should get the same answer.
- `LLMProvider`: small provider interface for model-based judges.
- `EvalStore`: SQLite persistence for runs, case results, dimension results, and human reviews.
- `ReportRenderer`: HTML report generation.
- `Review UI`: local browser workflow for human calibration.
- `ReviewSignal`: structured evidence produced by expert review.
- `Finding`: a grouped, recurring failure pattern.
- `EvalTarget`: a bounded improvement task with regression cases and success criteria.
- `GoldenLabel`: expert-labeled ground truth for one case and one dimension.

## Provider Strategy

The core does not depend on LangChain. The first supported production provider is OpenAI, but model calls go through `LLMProvider`. Users can add Anthropic, Gemini, internal gateways, or proxy providers by implementing `judge_json`.

This keeps the project small, easier to audit, and friendlier to future SaaS hosting.

## Data Flow

```text
CSV or Google Sheets export
  -> EvalCase[]
  -> Rubric
  -> EvaluationEngine
  -> Logic checks and LLM judge calls
  -> SQLite run storage
  -> HTML report
  -> Local human review UI
  -> human-machine agreement metrics
  -> ReviewSignal[]
  -> Finding[]
  -> EvalTarget
  -> Golden-set calibration
  -> Outcome correlation
```

## Self-Improving Loop

The project follows the principle that human judgment should become structured evidence, not just one-off approval.

```text
marketer correction
  -> review signal
  -> recurring finding
  -> eval target
  -> targeted improvement
  -> regression check
```

The CLI exposes this as:

```bash
evalkit signals
evalkit findings
evalkit targets
```

For non-technical users, the same loop can run through one command:

```bash
evalkit learn
```

Ambiguous cases should stay routed to human review. The goal is not to automate taste away; the goal is to make expert judgment compound.

## Golden Sets And Backtesting

Golden sets are stored as CSVs with one row per `case_id` and `dimension_name`. They let teams compare evaluator outputs against trusted labels.

The CLI exposes:

```bash
evalkit init
evalkit suggest-rubric
evalkit calibrate
evalkit outcomes
evalkit backtest
```

Backtesting runs a historical dataset through the current rubric/provider, then compares results against golden labels and optional business outcome metrics. This helps teams decide whether a new rubric, prompt, model, or workflow is actually better.
