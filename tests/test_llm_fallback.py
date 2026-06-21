import asyncio

import pytest

from callforge.infrastructure.llm.base import LLMProvider, LLMResult, LLMUnavailableError
from callforge.infrastructure.llm.fallback import FallbackLLMProvider
from callforge.infrastructure.llm.mock_provider import MockProvider


class FailingProvider(LLMProvider):
    name = "failing"

    async def complete(self, system, messages, json_mode=False) -> LLMResult:
        raise ConnectionError("provider down")


def test_falls_back_to_next_provider():
    chain = FallbackLLMProvider([FailingProvider(), MockProvider()])
    result = asyncio.run(
        chain.complete("You are the RouterAgent.", [{"role": "user", "content": "hola"}])
    )
    assert result.provider == "mock"


def test_raises_when_all_providers_fail():
    chain = FallbackLLMProvider([FailingProvider(), FailingProvider()])
    with pytest.raises(LLMUnavailableError):
        asyncio.run(chain.complete("system", [{"role": "user", "content": "hola"}]))


def test_first_provider_wins_when_healthy():
    chain = FallbackLLMProvider([MockProvider(), FailingProvider()])
    result = asyncio.run(
        chain.complete("You are the SupportAgent.", [{"role": "user", "content": "hola"}])
    )
    assert result.provider == "mock"


def test_provider_stream_default_yields_full_text():
    chain = FallbackLLMProvider([MockProvider()])

    async def collect():
        return [
            piece
            async for piece in chain.stream(
                "You are CompanionAgent.", [{"role": "user", "content": "hola"}]
            )
        ]

    pieces = asyncio.run(collect())
    assert pieces and "".join(pieces)  # mock has no real streaming -> one chunk


def test_llm_primary_ollama_puts_ollama_first():
    from callforge.config import Settings
    from callforge.infrastructure.llm.fallback import build_provider_chain

    base = dict(_env_file=None, groq_api_key="k", ollama_enabled=True, mock_enabled=True)
    groq_first = build_provider_chain(Settings(**base, llm_primary="groq"))
    assert [p.name for p in groq_first.providers] == ["groq", "ollama", "mock"]

    ollama_first = build_provider_chain(Settings(**base, llm_primary="ollama"))
    assert [p.name for p in ollama_first.providers] == ["ollama", "groq", "mock"]


def test_remote_brain_is_first_with_local_fallback():
    from callforge.config import Settings
    from callforge.infrastructure.llm.fallback import build_provider_chain

    chain = build_provider_chain(
        Settings(
            _env_file=None, groq_api_key="k", ollama_enabled=True, mock_enabled=True,
            llm_primary="ollama", ollama_remote_url="http://1.2.3.4:11434",
            ollama_remote_model="big-model",
        )
    )
    # remote big brain first, local model as fallback, then groq, then mock
    assert [p.name for p in chain.providers] == [
        "ollama-remote", "ollama", "groq", "mock"
    ]


def test_remote_brain_down_falls_through_to_local():
    # An unreachable remote must not break the turn — the chain moves on.
    from callforge.config import Settings
    from callforge.infrastructure.llm.fallback import build_provider_chain

    chain = build_provider_chain(
        Settings(
            _env_file=None, groq_api_key="", ollama_enabled=False, mock_enabled=True,
            llm_primary="ollama", ollama_remote_url="http://127.0.0.1:1",
        )
    )
    result = asyncio.run(
        chain.complete("You are CompanionAgent.", [{"role": "user", "content": "hola"}])
    )
    assert result.provider == "mock"  # remote dead -> falls through to mock
