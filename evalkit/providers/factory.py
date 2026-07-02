from __future__ import annotations

from evalkit.errors import UserFacingError
from evalkit.providers.base import LLMProvider
from evalkit.providers.heuristic import HeuristicProvider


def make_provider(name: str, *, openai_api_key: str | None = None, ollama_base_url: str | None = None) -> LLMProvider:
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

        return OpenAIProvider(api_key=openai_api_key)
    if name == "ollama":
        from evalkit.providers.ollama_provider import OllamaProvider

        return OllamaProvider(base_url=ollama_base_url)
    raise UserFacingError(f"Unknown provider '{name}'. Supported providers: heuristic, openai, ollama.")
