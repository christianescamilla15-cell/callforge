def _start(client) -> str:
    response = client.post(
        "/api/v1/conversations/start",
        json={"customer_external_id": "ext-1", "customer_name": "Ana"},
    )
    assert response.status_code == 200
    return response.json()["conversation_id"]


def test_full_conversation_flow_persists_messages(client):
    conversation_id = _start(client)

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Hola, tengo una duda sobre mi plan"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert body["agent_used"] == "support"
    assert body["escalated"] is False
    assert 0.0 <= body["confidence"] <= 1.0

    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["customer", "assistant"]
    assert detail["status"] == "active"
    assert detail["intent"] == "question"


def test_technical_message_uses_troubleshooting_agent(client):
    conversation_id = _start(client)
    body = client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Mi modem no funciona, el internet se cae"},
    ).json()
    assert body["agent_used"] == "troubleshooting"
    assert body["intent"] == "technical_issue"


def test_message_to_unknown_conversation_returns_404(client):
    response = client.post(
        "/api/v1/conversations/nope/message", json={"content": "hola"}
    )
    assert response.status_code == 404


def test_reusing_external_id_reuses_customer(client):
    first = client.post(
        "/api/v1/conversations/start", json={"customer_external_id": "ext-9"}
    ).json()
    second = client.post(
        "/api/v1/conversations/start", json={"customer_external_id": "ext-9"}
    ).json()
    assert first["customer_id"] == second["customer_id"]
