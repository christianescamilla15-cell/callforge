from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from callforge.infrastructure.llm.base import LLMProvider, LLMResult


class OllamaProvider(LLMProvider):
    """Local Ollama chat endpoint. Zero cost."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1",
        timeout: float = 60.0,
        keep_alive: str = "30m",
        num_predict: int = 512,
        num_ctx: int = 8192,
        temperature: float = 0.3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        # keep_alive keeps the model resident in VRAM between turns (no cold
        # reload); num_predict caps generation so it can't ramble; num_ctx is
        # the context window (Ollama defaults to a tiny 4096 regardless of what
        # the model supports, which silently truncates memories + history).
        self._keep_alive = keep_alive
        self._num_predict = num_predict
        self._num_ctx = num_ctx
        self._temperature = temperature

    def _options(self) -> dict:
        return {
            "temperature": self._temperature,
            "num_predict": self._num_predict,
            "num_ctx": self._num_ctx,
        }

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
    ) -> LLMResult:
        payload: dict = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": self._options(),
        }
        if json_mode:
            payload["format"] = "json"

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._base_url}/api/chat", json=payload)
            response.raise_for_status()
        latency_ms = int((time.perf_counter() - started) * 1000)

        data = response.json()
        return LLMResult(
            text=data["message"]["content"],
            provider=self.name,
            model=self._model,
            tokens_in=data.get("prompt_eval_count", 0),
            tokens_out=data.get("eval_count", 0),
            latency_ms=latency_ms,
            estimated_cost=0.0,
        )

    async def stream(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Yield content deltas from Ollama's streaming chat (NDJSON)."""
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "keep_alive": self._keep_alive,
            "options": self._options(),
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
