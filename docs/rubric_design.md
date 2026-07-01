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
