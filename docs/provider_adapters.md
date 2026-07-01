# Provider Adapters

`evalkit` uses a tiny provider interface:

```python
class LLMProvider(Protocol):
    name: str

    def judge_json(self, *, system_prompt: str, user_prompt: str, model: str | None) -> dict:
        ...
```

The returned dictionary should include:

- `passed`: boolean
- `score`: number, usually 1 to 5
- `rationale`: short explanation

To add a provider, create a class with the same method and register it in `evalkit/providers/factory.py`.

The project intentionally avoids a heavy orchestration dependency in v1. If a user already runs through LangChain, LiteLLM, or an internal gateway, they can wrap that system in this adapter.
