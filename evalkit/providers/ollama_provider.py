from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from evalkit.errors import UserFacingError


class OllamaProvider:
    """Local open-source model provider using Ollama's local HTTP API."""

    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("EVALKIT_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")

    def judge_json(self, *, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        resolved_model = model or os.getenv("EVALKIT_OLLAMA_MODEL")
        if not resolved_model:
            raise UserFacingError(
                "No Ollama judge model was provided.\n"
                "Fix: pass --model MODEL_NAME or export EVALKIT_OLLAMA_MODEL='MODEL_NAME'.\n"
                "Example: ollama pull llama3.1 && evalkit run --provider ollama --model llama3.1 ..."
            )

        request_body = {
            "model": resolved_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise UserFacingError(
                f"Could not reach Ollama at {self.base_url}.\n"
                "Fix: install Ollama, start it, and pull a model.\n"
                "Example: ollama pull llama3.1\n"
                f"Provider detail: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise UserFacingError(
                "Ollama returned a response that was not valid JSON.\n"
                "Fix: check that your local Ollama server is healthy and retry."
            ) from exc

        content = (payload.get("message") or {}).get("content", "")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise UserFacingError(
                "The local model did not return valid JSON for the judge result.\n"
                "Fix: retry with a stronger instruction-following model, or use --provider heuristic/openai.\n"
                f"Raw model response: {content[:500]}"
            ) from exc
        return parsed
