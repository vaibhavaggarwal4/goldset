from __future__ import annotations

import json
import os

from openai import OpenAI

from evalkit.errors import UserFacingError


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            raise UserFacingError(
                "OPENAI_API_KEY is not set.\n"
                "Fix: export OPENAI_API_KEY='your_key' before running with --provider openai."
            )
        self.client = OpenAI(api_key=resolved_key)

    def judge_json(self, *, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        resolved_model = model or os.getenv("EVALKIT_OPENAI_MODEL")
        if not resolved_model:
            raise UserFacingError(
                "No OpenAI judge model was provided.\n"
                "Fix: pass --model MODEL_NAME or export EVALKIT_OPENAI_MODEL='MODEL_NAME'."
            )

        try:
            response = self.client.responses.create(
                model=resolved_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise UserFacingError(
                f"OpenAI request failed for model '{resolved_model}'.\n"
                "Fix: check your API key, model name, account access, and network connection.\n"
                f"Provider detail: {exc}"
            ) from exc
        text = response.output_text
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise UserFacingError(
                "OpenAI returned a response that was not valid JSON.\n"
                "Fix: retry, or choose a model that supports structured JSON output.\n"
                f"Raw response: {text[:500]}"
            ) from exc
