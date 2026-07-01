# Rubric Design

A good marketing eval rubric separates three kinds of judgment:

- Logic checks for deterministic constraints such as character limits, required claims, forbidden phrases, and schema validity.
- LLM judges for qualitative judgment such as clarity, specificity, audience fit, brand voice, and strategic quality.
- Human review for calibration, high-risk decisions, and dimensions where taste or company context matters.

Prefer binary pass/fail dimensions when possible. Scores can be useful, but binary judgments create clearer gates and better calibration conversations.

## Golden Sets

A golden set should include:

- Obvious passes
- Obvious failures
- Borderline examples
- Examples that fail only one dimension
- Examples that expose known landmines

Start with 20 to 50 examples per surface. Expand when human reviewers and LLM judges disagree for reasons the rubric does not explain.

## Golden Set CSV Format

Goldset uses one label per case and dimension:

```csv
case_id,dimension_name,expected_passed,expected_score,labeler,notes
email-001,clarity,true,5,expert,Clear message and CTA
email-001,audience_fit,false,2,expert,Too generic for founder audience
```

This lets teams measure precision, recall, false positives, false negatives, and per-dimension evaluator reliability.
