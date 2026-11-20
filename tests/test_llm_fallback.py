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
                "You are the SupportAgent.", [{"role": "user", "content": "hola"}]
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
        chain.complete("You are the SupportAgent.", [{"role": "user", "content": "hola"}])
    )
    assert result.provider == "mock"  # remote dead -> falls through to mock


def test_el_mock_es_la_red_final_aunque_este_desactivado_si_no_hay_otro():
    """Sin Groq ni Ollama, el MockProvider entra aunque MOCK_ENABLED=false: el sistema nunca
    se queda sin quién conteste."""
    from callforge.config import Settings
    from callforge.infrastructure.llm.fallback import build_provider_chain

    solo = build_provider_chain(Settings(_env_file=None, groq_api_key="", ollama_enabled=False,
                                         mock_enabled=False))
    assert [p.name for p in solo.providers] == ["mock"]
    sin_mock = build_provider_chain(Settings(_env_file=None, groq_api_key="k", ollama_enabled=True,
                                             mock_enabled=False))
    assert [p.name for p in sin_mock.providers] == ["groq", "ollama"]


def test_la_cadena_vacia_no_se_puede_construir():
    with pytest.raises(ValueError):
        FallbackLLMProvider([])


def test_todos_los_proveedores_caidos_la_api_responde_con_el_mensaje_controlado(tmp_path, monkeypatch):
    """Groq, Ollama y Mock fallan: la API responde 200 con el mensaje controlado, deja el evento
    fallback_used y el error en la corrida, y no crea ticket. El sistema no se cae."""
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    import callforge.presentation.api.app as modulo_app
    from callforge.infrastructure.voice.phrases import CONTROLLED_FALLBACK
    from tests.conftest import make_test_settings

    class Caido(LLMProvider):
        def __init__(self, name):
            self.name = name

        async def complete(self, system, messages, json_mode=False):
            raise ConnectionError(f"{self.name} caído")

    monkeypatch.setattr(modulo_app, "build_provider_chain",
                        lambda _s: FallbackLLMProvider([Caido("groq"), Caido("ollama"), Caido("mock")]))
    with TestClient(modulo_app.create_app(make_test_settings(tmp_path))) as client:
        cid = client.post("/api/v1/conversations/start", json={}).json()["conversation_id"]
        respuesta = client.post(f"/api/v1/conversations/{cid}/message", json={"content": "Hola"})
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["reply"] == CONTROLLED_FALLBACK
        assert cuerpo["escalated"] is False and cuerpo["ticket_id"] is None
        assert client.get("/api/v1/tickets").json() == []
        with client.app.state.engine.connect() as conexion:
            error = conexion.execute(text("SELECT error FROM agent_runs")).scalar_one()
            evento = conexion.execute(text("SELECT event_type FROM agent_events")).scalar_one()
    assert error == "groq: groq caído; ollama: ollama caído; mock: mock caído"  # en orden de la cadena
    assert evento == "fallback_used"


def test_costo_estimado_por_millon_de_tokens_y_modelo_desconocido_gratis():
    from decimal import Decimal

    from callforge.infrastructure.llm.base import estimate_cost

    costo = estimate_cost("llama-3.1-8b-instant", 1_000_000, 500_000)
    assert Decimal(str(costo)) == Decimal("0.05") + Decimal("0.04")
    assert estimate_cost("llama-3.3-70b-versatile", 2_000, 1_000) == pytest.approx(
        float((Decimal(2_000) * Decimal("0.59") + Decimal(1_000) * Decimal("0.79")) / 1_000_000))
    assert estimate_cost("mock", 10_000, 10_000) == 0.0
    assert estimate_cost("modelo-local-cualquiera", 10_000, 10_000) == 0.0
