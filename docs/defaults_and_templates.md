# Defaults And Templates

Goldset Evals ships with editable templates so users do not have to start from a blank YAML file.

## Rubric Templates

Templates live in:

```text
templates/rubrics/
```

Current templates:

- `basic_marketing_quality.yaml`
- `lifecycle_email.yaml`
- `paid_social_ad.yaml`
- `landing_page.yaml`

Users should copy a template, rename it, and edit thresholds, dimensions, or reviewer requirements.

Example:

```bash
cp templates/rubrics/lifecycle_email.yaml my-lifecycle-rubric.yaml
```

Then run:

```bash
evalkit run --rubric my-lifecycle-rubric.yaml --input examples/lifecycle_email/sample.csv
```

## Sample Data

Sample CSV files live in:

```text
examples/
```

The broad sample file is:

```text
examples/sample_data/marketing_outputs.csv
```

This file includes lifecycle email, paid social ad, landing page, and SEO/content brief examples. It is meant for first-run testing and for understanding the expected CSV shape.

Golden set and outcome examples live in:

```text
examples/golden_sets/
examples/outcomes/
```

## Default Rule Types

Logic rules are deterministic checks. Current rules include:

- `max_chars`
- `min_chars`
- `contains_cta`
- `required_terms`
- `forbidden_terms`
- `regex`

LLM judge and human review dimensions can be edited directly in YAML.

## Recommended User Path

1. Run the built-in sample.
2. Copy the closest rubric template.
3. Replace sample rows with real campaign outputs.
4. Tighten thresholds and dimensions after the first few human reviews.
