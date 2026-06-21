from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


class LLMUnavailableError(Exception):
    """Raised when every provider in the chain failed."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    estimated_cost: float = 0.0


# USD per 1M tokens (input, output). Unknown models cost 0 (local/mock).
_PRICES: dict[str, tuple[float, float]] = {
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = _PRICES.get(model, (0.0, 0.0))
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
    ) -> LLMResult:
        """messages: [{"role": "user"|"assistant", "content": str}, ...]"""

    async def stream(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Yield text deltas as they are generated. Default: no real
        streaming — produce the whole answer at once (providers that support
        token streaming override this)."""
        result = await self.complete(system, messages, json_mode=False)
        if result.text:
            yield result.text
