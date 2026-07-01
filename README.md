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
- CSV files, including Google Sheets exports
- OpenAI as the first production LLM provider, with a small provider interface for adding others

The first complete example is lifecycle email. Starter rubrics are also included for paid social ads, landing pages, and SEO/content briefs.

## Requirements

- Python 3.10 or newer
- macOS, Linux, or Windows
- OpenAI API key only if you want to run real LLM judge calls

You can run the demo without an API key by using the built-in `heuristic` provider.

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
evalkit run \
  --rubric examples/lifecycle_email/rubric.yaml \
  --input examples/lifecycle_email/sample.csv \
  --db evalkit.sqlite \
  --suite-name "Lifecycle Email Evaluation" \
  --provider heuristic \
  --report lifecycle-report.html
```

Open the report:

```bash
open lifecycle-report.html
```

On Windows:

```powershell
start lifecycle-report.html
```

On Linux:

```bash
xdg-open lifecycle-report.html
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

Review dimensions, mark pass/fail, add notes, and submit. Stop the server with `Ctrl+C`.

Then regenerate the report so human review metrics are included:

```bash
evalkit report --db evalkit.sqlite --run-id latest --output lifecycle-report.html
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

After submitting human reviews, extract structured signals:

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
  --db evalkit.sqlite \
  --report lifecycle-report.html
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
evalkit run --rubric RUBRIC.yaml --input DATA.csv
evalkit report --db evalkit.sqlite --run-id latest --output report.html
evalkit review --db evalkit.sqlite --run-id latest --port 8765
evalkit signals --db evalkit.sqlite --run-id latest
evalkit findings --db evalkit.sqlite --run-id latest
evalkit targets --db evalkit.sqlite --finding-id FINDING_ID
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
examples/lifecycle_email/rubric.yaml
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
