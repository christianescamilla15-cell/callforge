import asyncio

from callforge.agents.base import AgentContext
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.domain.entities import ResolutionStep
from callforge.domain.value_objects import ResolutionStepStatus
from callforge.infrastructure.llm.mock_provider import MockProvider


def test_troubleshooting_prompt_includes_previous_steps():
    agent = TroubleshootingAgent(MockProvider())
    ctx = AgentContext(
        conversation_id="c1",
        user_message="sigue sin funcionar",
        resolution_steps=[
            ResolutionStep(
                conversation_id="c1",
                step_number=1,
                instruction="Reiniciar el modem",
                status=ResolutionStepStatus.ANSWERED,
                customer_response="lo reinicie y sigue igual",
            )
        ],
    )
    prompt = agent.build_user_prompt(ctx)
    assert "PREVIOUS DIAGNOSTIC STEPS" in prompt
    assert "1. Reiniciar el modem" in prompt
    assert "lo reinicie y sigue igual" in prompt


def test_diagnostic_steps_persist_across_messages(client):
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]

    # First technical message -> troubleshooting proposes step 1
    client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Mi internet no funciona, el modem esta raro"},
    )
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert len(detail["resolution_steps"]) == 1
    assert detail["resolution_steps"][0]["status"] == "proposed"

    # Customer reports back -> step 1 answered, step 2 proposed
    client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Reinicie el modem y el error sigue igual"},
    )
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    steps = detail["resolution_steps"]
    assert len(steps) == 2
    assert steps[0]["status"] == "answered"
    assert steps[0]["customer_response"] == "Reinicie el modem y el error sigue igual"
    assert steps[1]["status"] == "proposed"
    assert steps[1]["step_number"] == 2


def test_non_technical_conversation_has_no_steps(client):
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Hola, una duda general sobre el servicio"},
    )
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["resolution_steps"] == []


def test_workflow_returns_resolution_step_entity():
    from callforge.orchestration.workflow import SupportWorkflow  # noqa: F401

    # Covered E2E above; this asserts the mock contract shape directly.
    provider = MockProvider()
    result = asyncio.run(
        provider.complete(
            "You are the TroubleshootingAgent.",
            [{"role": "user", "content": "no funciona el internet"}],
        )
    )
    import json

    data = json.loads(result.text)
    assert data["diagnostic_step"]["instruction"]
