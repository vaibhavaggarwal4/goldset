from __future__ import annotations

from evalkit.errors import UserFacingError
from evalkit.providers.base import LLMProvider
from evalkit.providers.heuristic import HeuristicProvider


def make_provider(name: str) -> LLMProvider:
    if name == "heuristic":
        return HeuristicProvider()
    if name == "openai":
        try:
            from evalkit.providers.openai_provider import OpenAIProvider
        except ModuleNotFoundError as exc:
            if exc.name == "openai":
                raise UserFacingError(
                    "OpenAI support is not installed.\n"
                    'Fix: run python -m pip install -e ".[openai]" inside your virtual environment.'
                ) from exc
            raise

        return OpenAIProvider()
    raise UserFacingError(f"Unknown provider '{name}'. Supported providers: heuristic, openai.")
