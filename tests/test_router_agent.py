import asyncio

from callforge.agents.base import AgentContext
from callforge.agents.router_agent import RouterAgent
from callforge.infrastructure.llm.mock_provider import MockProvider


def _route(message: str) -> dict:
    agent = RouterAgent(MockProvider())
    ctx = AgentContext(conversation_id="c1", user_message=message)
    result = asyncio.run(agent.run(ctx))
    assert not result.failed
    return result.data


def test_routes_billing_to_support():
    data = _route("Tengo una duda sobre mi factura de este mes")
    assert data["intent"] == "billing"
    assert data["next_agent"] == "support"


def test_routes_technical_issue_to_troubleshooting():
    data = _route("Mi internet no funciona desde ayer")
    assert data["intent"] == "technical_issue"
    assert data["next_agent"] == "troubleshooting"


def test_routes_human_request_to_escalation():
    data = _route("Quiero hablar con un humano ahora mismo")
    assert data["next_agent"] == "escalation"
    assert data["urgency"] in ("high", "critical")


def test_router_reports_confidence():
    data = _route("Hola, una pregunta general")
    assert 0.0 <= float(data["confidence"]) <= 1.0
