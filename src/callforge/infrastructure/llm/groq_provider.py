from __future__ import annotations

import time

import httpx

from callforge.infrastructure.llm.base import LLMProvider, LLMResult, estimate_cost


class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat completions endpoint."""

    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
    ) -> LLMResult:
        payload: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.3,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        latency_ms = int((time.perf_counter() - started) * 1000)

        data = response.json()
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        return LLMResult(
            text=data["choices"][0]["message"]["content"],
            provider=self.name,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            estimated_cost=estimate_cost(self._model, tokens_in, tokens_out),
        )
