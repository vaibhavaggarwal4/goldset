from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    name: str

    def judge_json(self, *, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        """Return parsed JSON with at least passed, score, and rationale keys."""
