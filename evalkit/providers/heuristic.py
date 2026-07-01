from __future__ import annotations

import hashlib


class HeuristicProvider:
    """Offline provider for demos, tests, and smoke runs."""

    name = "heuristic"

    def judge_json(self, *, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        digest = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
        bucket = int(digest[:2], 16)
        passed = bucket % 5 != 0
        score = 4 if passed else 2
        return {
            "passed": passed,
            "score": score,
            "rationale": "Offline heuristic judgment. Use an LLM provider for production-quality evaluation.",
        }
