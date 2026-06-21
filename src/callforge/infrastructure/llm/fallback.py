"""Provider chain with graceful degradation: try each provider in order,
collect failures, surface which provider actually answered."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from callforge.config import Settings
from callforge.infrastructure.llm.base import LLMProvider, LLMResult, LLMUnavailableError
from callforge.infrastructure.llm.groq_provider import GroqProvider
from callforge.infrastructure.llm.mock_provider import MockProvider
from callforge.infrastructure.llm.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class FallbackLLMProvider(LLMProvider):
    name = "fallback-chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider needs at least one provider")
        self.providers = providers

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
    ) -> LLMResult:
        errors: list[str] = []
        for provider in self.providers:
            try:
                return await provider.complete(system, messages, json_mode=json_mode)
            except Exception as exc:  # noqa: BLE001 - any provider failure moves to next
                logger.warning("LLM provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise LLMUnavailableError("; ".join(errors))

    async def stream(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Stream from the first provider that yields anything. If a provider
        fails before producing output, fall through to the next."""
        errors: list[str] = []
        for provider in self.providers:
            try:
                produced = False
                async for piece in provider.stream(system, messages):
                    produced = True
                    yield piece
                if produced:
                    return
            except Exception as exc:  # noqa: BLE001 - move to next provider
                logger.warning("LLM stream provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        raise LLMUnavailableError("; ".join(errors))


def build_provider_chain(settings: Settings) -> FallbackLLMProvider:
    groq = (
        GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            timeout=settings.llm_timeout_seconds,
        )
        if settings.groq_api_key
        else None
    )
    ollama = (
        OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout=max(settings.llm_timeout_seconds, 60.0),
            keep_alive=settings.ollama_keep_alive,
            num_predict=settings.ollama_num_predict,
            num_ctx=settings.ollama_num_ctx,
            temperature=settings.ollama_temperature,
        )
        if settings.ollama_enabled
        else None
    )
    # Remote GPU "big brain" (on-demand cloud/LAN box). Answers first when set;
    # the local model below is the automatic fallback if the box is unreachable.
    remote = (
        OllamaProvider(
            base_url=settings.ollama_remote_url,
            model=settings.ollama_remote_model or settings.ollama_model,
            timeout=max(settings.llm_timeout_seconds, 90.0),
            keep_alive=settings.ollama_keep_alive,
            num_predict=settings.ollama_num_predict,
            num_ctx=settings.ollama_num_ctx,
            temperature=settings.ollama_temperature,
        )
        if settings.ollama_remote_url
        else None
    )
    if remote is not None:
        remote.name = "ollama-remote"  # distinguish from local in telemetry

    # llm_primary="ollama" makes the LOCAL stack answer first (remote big brain,
    # then local model); the cloud provider becomes the fallback. Default groq.
    if settings.llm_primary == "ollama":
        ordered = [remote, ollama, groq]
    else:
        ordered = [groq, remote, ollama]

    providers: list[LLMProvider] = [p for p in ordered if p is not None]
    if settings.mock_enabled or not providers:
        providers.append(MockProvider())
    return FallbackLLMProvider(providers)
