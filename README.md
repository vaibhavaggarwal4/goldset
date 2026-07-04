# Goldset Evals

Open source evals and review workflows for AI go-to-market teams.


## What This Is

`evalkit` helps teams evaluate AI-generated marketing work before it reaches customers.

It supports:

- deterministic checks for things like length, required terms, forbidden terms, and CTAs
- LLM-as-judge evaluations for qualitative dimensions like clarity, audience fit, and brand voice
- SQLite storage for evaluation runs
- HTML reports
- a local human review UI for calibration
- structured review signals, findings, and eval targets for self-improving workflows
- golden sets, calibration, evaluator reliability metrics, outcome correlation, and backtesting
- CSV files, including Google Sheets exports
- OpenAI and Ollama providers, with a small provider interface for adding others

The first complete example is lifecycle email. Editable default rubrics are also included for general marketing copy, paid social ads, landing pages, and SEO/content briefs.

## Requirements

- Python 3.10 or newer
- macOS, Linux, or Windows
- OpenAI API key only if you want to run real LLM judge calls
- Ollama only if you want to run local open-source models

You can run the demo without an API key or local model by using the built-in `heuristic` provider.

## Quickstart

Clone the repo, then run these commands from the project directory.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
evalkit doctor
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.venv\Scripts\Activate.ps1
```

If `evalkit doctor` says setup looks good, run the lifecycle email example:

```bash
evalkit ui
```

Open the printed URL, choose `examples/lifecycle_email/rubric.yaml` and `examples/lifecycle_email/sample.csv`, then click **Run eval**.

You can also run the same example from the CLI:

```bash
evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --db evalkit.sqlite \
  --suite-name "Lifecycle Email Evaluation" \
  --provider heuristic
```

The command prints a clickable report URL when it finishes.

## Local Workbench

The local workbench is the easiest way to use Goldset without memorizing commands:

```bash
evalkit ui
```

It opens a local web UI for:

- guided setup, results, review, learning, calibration, and backtesting steps
- running evals from a rubric and CSV
- choosing rubric, CSV, golden set, and outcomes files with the native file picker
- configuring provider setup for offline heuristic runs, OpenAI API keys, or local Ollama models
- starting new workflows and reviewing previous workflows
- categorizing runs by campaign, channel, surface, or experiment
- viewing run history and pass rates
- generating HTML reports
- reviewing machine judgments
- extracting review signals and findings
- calibrating against a golden set
- correlating eval results with business outcomes
- running historical backtests
- showing progress while evals, reports, calibration, and setup tasks are running

By default it uses `evalkit.sqlite`. To open another project database:

```bash
evalkit ui --db my_lifecycle_eval/evalkit.sqlite
```

When you select a file in the workbench, Goldset copies it into a local `.goldset/uploads/` folder and runs from that copy.

When you choose `OpenAI` in the workbench, Goldset shows an API key field and model guidance. The key is used for that local run and is not saved to SQLite. You can also keep using `OPENAI_API_KEY` and `EVALKIT_OPENAI_MODEL` environment variables.

When you choose `Ollama`, Goldset can set up the local model after you confirm. On macOS with Homebrew, it can install Ollama, start the local server, and pull a model such as `llama3.1`. On other systems, it will explain the manual install step if automatic setup is not available.

Golden sets are best created before you rely on a new rubric, model, or prompt route. Calibration compares evaluator output against those expert labels, so in practice you run a known historical set first, calibrate reliability, then use the setup on new campaign work.

## Recommended Workflow

Goldset works best when you treat evals as an iterative system, not a one-time score.

1. Install and run the sample.
2. Define the rubric, or start from suggested dimensions.
3. Add a golden set of expert-labeled examples. You can skip this at first, but evaluator quality will be harder to trust.
4. Import campaign exports and outcome data if your data is not already in Goldset format.
5. Run evals.
6. Review failures and add corrections.
7. Run `evalkit learn`.
8. Improve the prompt, rubric, workflow, or model.
9. Backtest against the golden set and outcome data.

Create an editable workspace:

```bash
evalkit init --surface lifecycle_email --name my_lifecycle_eval
```

See suggested dimensions before creating files:

```bash
evalkit suggest-rubric --surface lifecycle_email
```

Inspect and import a messy campaign export:

```bash
evalkit inspect-csv --source examples/exports/generic_email_export.csv

evalkit import \
  --source examples/exports/generic_email_export.csv \
  --mapping templates/mappings/generic_email.yaml \
  --output my_lifecycle_eval/data.csv

evalkit import-outcomes \
  --source examples/exports/generic_email_results.csv \
  --mapping templates/mappings/generic_outcomes.yaml \
  --output my_lifecycle_eval/outcomes.csv
```

You can also test with the broader sample CSV:

```bash
evalkit run \
  --rubric templates/rubrics/basic_marketing_quality.yaml \
  --input examples/sample_data/marketing_outputs.csv \
  --provider heuristic
```

## Reports

By default, HTML reports are written to `reports/` with a name based on the suite and run ID.

If a report path already exists, Goldset does not overwrite it. It appends a number so multiple reports can live side by side, for example `lifecycle-email-evaluation-abc12345-2.html`.

You can choose a specific report location:

```bash
evalkit run --rubric RUBRIC.yaml --input DATA.csv --report reports/my-report.html
```

## Human Review UI

After running an eval, start the local review UI:

```bash
evalkit review --db evalkit.sqlite --run-id latest --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

Review dimensions, mark pass/fail, add a failure reason or correction when something is wrong, and submit. Stop the server with `Ctrl+C`.

Then regenerate the report so human review metrics are included:

```bash
evalkit report --db evalkit.sqlite --run-id latest
```

## Self-Improving Loop

Goldset Evals is designed around a simple learning loop:

```text
human review
  -> structured review signal
  -> recurring finding
  -> eval target
  -> prompt/rubric/workflow/model improvement
  -> regression check
```

For non-technical users, run the learning loop in one step:

```bash
evalkit learn --db evalkit.sqlite --run-id latest
```

Then refresh the report to see review signals, findings, and eval targets:

```bash
evalkit report --db evalkit.sqlite --run-id latest
```

To also export improvement task folders automatically:

```bash
evalkit learn --db evalkit.sqlite --run-id latest --export-targets --owner "growth-team"
```

Advanced users can run the loop step by step.

Extract structured signals:

```bash
evalkit signals --db evalkit.sqlite --run-id latest
```

Group signals into recurring findings:

```bash
evalkit findings --db evalkit.sqlite --run-id latest --min-cases 1
```

Create a bounded eval target from a finding:

```bash
evalkit targets --db evalkit.sqlite --finding-id 1 --owner "growth-team"
```

This exports a task folder under `eval-targets/` with:

- `task.yaml`
- `cases.csv`
- `README.md`

The target is meant to be the "hill to climb": a small, evidence-backed task for improving a prompt, rubric, workflow, model route, or product surface.

## Golden Sets, Calibration, And Backtesting

A golden set is a CSV of trusted labels from expert reviewers. It is used to test whether an evaluator is reliable.

Example golden set:

```text
examples/golden_sets/lifecycle_email_golden_set.csv
```

Run calibration against an existing eval run:

```bash
evalkit calibrate \
  --db evalkit.sqlite \
  --run-id latest \
  --golden-set examples/golden_sets/lifecycle_email_golden_set.csv
```

This reports evaluator reliability metrics such as accuracy, precision, recall, false positive rate, and false negative rate. It also reports calibration metrics such as human-machine agreement, human-human agreement when multiple reviewers exist, and reviewer-vs-golden performance.

To connect eval quality to business outcomes, provide an outcomes CSV:

```text
examples/outcomes/lifecycle_email_outcomes.csv
```

Then run:

```bash
evalkit outcomes \
  --db evalkit.sqlite \
  --run-id latest \
  --outcomes examples/outcomes/lifecycle_email_outcomes.csv
```

This computes simple Pearson correlations between pass/fail results and numeric outcome metrics such as CTR, activation rate, reply rate, conversion rate, or revenue.

To run a historical backtest in one command:

```bash
evalkit backtest \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --golden-set examples/golden_sets/lifecycle_email_golden_set.csv \
  --outcomes examples/outcomes/lifecycle_email_outcomes.csv \
  --provider heuristic
```

## Use Local Open-Source Models

Goldset Evals supports local model judging through Ollama. This avoids sending data to a hosted LLM provider.

Install Ollama, pull a model, and run:

```bash
ollama pull llama3.1
export EVALKIT_OLLAMA_MODEL="llama3.1"
evalkit doctor --check-ollama

evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --provider ollama
```

You can also pass the local model directly:

```bash
evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --provider ollama \
  --model llama3.1
```

## Use OpenAI

Install the optional OpenAI dependency:

```bash
python -m pip install -e ".[openai]"
```

Set your API key and judge model:

```bash
export OPENAI_API_KEY="your_key"
export EVALKIT_OPENAI_MODEL="your_judge_model"
```

Check OpenAI setup:

```bash
evalkit doctor --check-openai
```

Run the same eval with OpenAI:

```bash
evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --provider openai \
  --db evalkit.sqlite
```

You can also pass the model directly:

```bash
evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --provider openai \
  --model your_judge_model
```

## Commands

```bash
evalkit doctor
evalkit init --surface lifecycle_email --name my_lifecycle_eval
evalkit suggest-rubric --surface lifecycle_email
evalkit inspect-csv --source EXPORT.csv
evalkit import --source EXPORT.csv --mapping MAPPING.yaml --output DATA.csv
evalkit import-outcomes --source RESULTS.csv --mapping OUTCOME_MAPPING.yaml --output OUTCOMES.csv
evalkit run --rubric RUBRIC.yaml --input DATA.csv
evalkit report --db evalkit.sqlite --run-id latest
evalkit review --db evalkit.sqlite --run-id latest --port 8765
evalkit ui --db evalkit.sqlite --port 8766
evalkit learn --db evalkit.sqlite --run-id latest
evalkit signals --db evalkit.sqlite --run-id latest
evalkit findings --db evalkit.sqlite --run-id latest
evalkit targets --db evalkit.sqlite --finding-id FINDING_ID
evalkit calibrate --db evalkit.sqlite --run-id latest --golden-set GOLDEN_SET.csv
evalkit outcomes --db evalkit.sqlite --run-id latest --outcomes OUTCOMES.csv
evalkit backtest --rubric RUBRIC.yaml --input DATA.csv --golden-set GOLDEN_SET.csv
```

Use debug mode when reporting a bug:

```bash
evalkit --debug run --rubric examples/lifecycle_email/rubric.yaml --input examples/lifecycle_email/sample.csv
```

## CSV Format

CSV files can be local files or Google Sheets exports.

Recommended columns:

- `case_id`
- `artifact_type`
- `channel`
- `audience`
- `campaign_goal`
- `stage`
- `input`
- artifact fields such as `subject_line`, `body`, `headline`, `primary_text`
- `output`

The loader accepts these common output columns:

- `output`
- `content`
- `copy`

Rubric dimensions can target a specific field:

```yaml
- name: subject_line_length
  evaluator: logic
  field: subject_line
  rule: max_chars
  threshold: 55
```

Or evaluate the full output by omitting `field`.

## Import Mapping Format

If your campaign export does not match Goldset's CSV format, use a YAML mapping file.

Example:

```yaml
fields:
  case_id: campaign_id
  artifact_type:
    constant: lifecycle_email
  subject_line: subject
  body: body
  output:
    join:
      - subject
      - body
    separator: " "
```

Supported mapping specs:

- `target: source_column`
- `target: { column: source_column }`
- `target: { constant: value }`
- `target: { join: [col_a, col_b], separator: " " }`
- `target: { coalesce: [first_choice, fallback] }`

Start with:

```text
templates/mappings/generic_email.yaml
templates/mappings/generic_outcomes.yaml
```

## Rubric Format

Rubrics are YAML files.

Each dimension uses one of three evaluators:

- `logic`: deterministic rules such as `max_chars`, `min_chars`, `contains_cta`, `required_terms`, `forbidden_terms`, and `regex`
- `llm_judge`: qualitative evaluation through the configured LLM provider
- `human_review`: a manual review dimension

Example:

```yaml
name: lifecycle_email_quality
version: 0.1
artifact_type: lifecycle_email
dimensions:
  - name: subject_line_length
    evaluator: logic
    description: Subject line should stay short enough for inbox scanning.
    field: subject_line
    rule: max_chars
    threshold: 55

  - name: clarity
    evaluator: llm_judge
    description: The email communicates the core message quickly and concretely.
    field: body
    requires_human_review: true
```

Start by copying:

```text
templates/rubrics/basic_marketing_quality.yaml
templates/rubrics/lifecycle_email.yaml
templates/rubrics/paid_social_ad.yaml
templates/rubrics/landing_page.yaml
```

## Golden Set Format

Golden sets are CSV files with one row per `case_id` and `dimension_name`.

Required columns:

- `case_id`
- `dimension_name`
- `expected_passed`

Optional columns:

- `expected_score`
- `labeler`
- `notes`

Example:

```csv
case_id,dimension_name,expected_passed,expected_score,labeler,notes
email-001,clarity,true,5,expert,Clear message and CTA
email-001,audience_fit,false,2,expert,Too generic for founder audience
```

## Outcomes Format

Outcome CSVs need a `case_id` column and one or more numeric metric columns.

Example:

```csv
case_id,ctr,activation_rate,reply_rate
email-001,0.081,0.144,0.012
email-002,0.064,0.117,0.007
```

## Project Structure

```text
evalkit/
  cli.py
  models.py
  evaluators.py
  logic.py
  loaders.py
  metrics.py
  reports.py
  review_ui.py
  self_improvement.py
  storage.py
  providers/
examples/
  lifecycle_email/
  paid_social_ad/
  landing_page/
  seo_content_brief/
docs/
```

## Troubleshooting

Run:

```bash
evalkit doctor
```

Common fixes:

- `Input CSV not found`: check the `--input` path or export your Google Sheet as CSV.
- `Rubric file not found`: check the `--rubric` path.
- `Input CSV has no header row`: add columns like `case_id`, `input`, and `output`.
- `OpenAI support is not installed`: run `python -m pip install -e ".[openai]"`.
- `OPENAI_API_KEY is not set`: export your API key before using `--provider openai`.
- `No OpenAI judge model was provided`: set `EVALKIT_OPENAI_MODEL` or pass `--model`.
- `Could not start the review UI`: another process may be using the port. Try `--port 8766`.

For full tracebacks:

```bash
evalkit --debug run ...
```

## Commercial Path

The open source project is designed to stay useful on its own. A hosted SaaS could add:

- hosted dashboards
- team workspaces
- approval workflows
- scheduled evals
- integrations with ESPs, ad platforms, CMSs, and customer data tools
- reviewer assignment and permissions
- long-term monitoring and regression alerts
