# Mini PRD: Goldset Evals

## Overview

Goldset Evals is an open source evaluation and review system for AI-generated marketing work. It helps GTM teams turn human marketing judgment into a repeatable quality system.

The project is CLI-first in v1 and uses a neutral internal package name, `evalkit`, so the company or product name can change later.

## Product Thesis

AI marketing teams do not just need more generation. They need a quality and learning layer.

Goldset Evals helps teams move from ad hoc review to a self-improving loop:

```text
marketer correction
  -> structured review signal
  -> recurring finding
  -> eval target
  -> prompt/rubric/workflow/model improvement
  -> regression check
```

The goal is not to remove marketers from the loop. The goal is to make their judgment compound.

## Target Users

- Founders at early-stage startups
- Growth marketers
- Marketing operators
- Growth engineers
- Lifecycle/content leads
- AI-forward GTM teams

## Problem

AI makes it easy to generate more campaigns, emails, landing pages, ads, and content briefs. It does not automatically make those outputs trustworthy.

Teams need a way to answer:

- Is this output good?
- Is it on-brand?
- Is it accurate?
- Does it fit the audience and channel?
- What should be checked by code, by an LLM judge, or by a human?
- Are failures recurring?
- Is the system improving over time?

## V1 Scope

V1 is an open source Python CLI.

Core capabilities:

- Load marketing examples from CSV or Google Sheets export.
- Define rubrics in YAML.
- Run deterministic checks for objective criteria.
- Run LLM-as-judge checks for qualitative criteria.
- Store eval runs in SQLite.
- Generate HTML reports.
- Provide a local human review UI.
- Capture human review as structured review signals.
- Group signals into recurring findings.
- Export findings as bounded eval targets.

## First Use Case

Lifecycle email evaluation.

Example dimensions:

- Subject line length
- Body length
- CTA presence
- Clarity
- Audience fit
- Brand voice
- Human review for subjective dimensions

## Self-Improving Architecture

The system introduces three learning-loop objects:

- `ReviewSignal`: structured evidence from a human review, correction, disagreement, or rubric issue.
- `Finding`: a recurring pattern grouped from review signals.
- `EvalTarget`: a bounded improvement task with regression cases and success criteria.

This architecture mirrors the principle that production usage should create evidence. Human review is not just approval; it becomes the raw material for improving prompts, rubrics, workflows, model routing, or product surfaces.

## System Components

Core objects:

- `Rubric`
- `RubricDimension`
- `EvalCase`
- `EvalArtifact`
- `EvaluationEngine`
- `DimensionResult`
- `EvalStore`
- `ReviewSignal`
- `Finding`
- `EvalTarget`
- `LLMProvider`

Main modules:

- CLI runner
- CSV loader
- YAML rubric parser
- Logic checks
- LLM judge provider interface
- OpenAI provider
- SQLite storage
- HTML report renderer
- Local review UI
- Self-improvement loop utilities

## User Workflow

1. Clone the repo.
2. Install the CLI.
3. Run `evalkit doctor`.
4. Run the lifecycle email example.
5. Open the HTML report.
6. Start the local human review UI.
7. Submit human judgments, corrections, and failure reasons.
8. Run `evalkit signals`.
9. Run `evalkit findings`.
10. Run `evalkit targets` to export a bounded improvement task.

## LLM Strategy

OpenAI is the first supported production provider.

The system does not depend on LangChain in v1. Instead, it uses a lightweight `LLMProvider` interface so users can add Anthropic, Gemini, LiteLLM, LangChain, or internal model gateways without changing the core eval architecture.

## Future SaaS Direction

The open source project remains useful as a local tool. A hosted SaaS could add:

- hosted dashboards
- team workspaces
- scheduled eval runs
- approval workflows
- reviewer assignment
- integrations with ESPs, CMSs, CRM tools, and ad platforms
- longitudinal quality monitoring
- regression alerts
- model comparison
- permissions and audit trails
- automatic finding detection across production traces

## Positioning

Goldset Evals is the open source quality and learning layer for AI go-to-market teams.

Potential tagline:

**Goldset Evals: open source quality control for self-improving AI marketing systems.**
