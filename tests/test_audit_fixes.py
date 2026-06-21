"""Tests for the audit-driven fixes: escalation guard, management
endpoints, pagination, and WS conversation resume."""


def _start(client) -> str:
    return client.post("/api/v1/conversations/start", json={}).json()["conversation_id"]


def test_first_block_drops_hallucinated_dialogue():
    from callforge.orchestration.workflow import _first_block

    hallucinated = (
        "¿Qué te pasa? Aquí estoy para escucharte.\n\n"
        "Estoy agobiado con el trabajo.\n\nSí, lo entiendo, ¿quieres hablar?"
    )
    assert _first_block(hallucinated) == "¿Qué te pasa? Aquí estoy para escucharte."
    assert _first_block("Una sola frase limpia.") == "Una sola frase limpia."


def test_companion_streaming_over_websocket(tmp_path):
    from fastapi.testclient import TestClient

    from callforge.presentation.api.app import create_app
    from tests.conftest import make_test_settings

    settings = make_test_settings(tmp_path)
    settings.companion_mode = True
    app = create_app(settings)
    with TestClient(app) as client:
        with client.websocket_connect("/webchat/ws") as ws:
            ws.receive_json()  # session
            ws.send_text("hola, cuentame algo")
            # Mock has no real streaming -> at least one chunk, then done
            chunks, done = [], None
            for _ in range(10):
                msg = ws.receive_json()
                if msg["type"] == "reply_chunk":
                    chunks.append(msg["text"])
                elif msg["type"] == "reply_done":
                    done = msg
                    break
            assert chunks
            assert done is not None
            assert done["reply"]
            assert done["agent_used"] == "companion"


def test_companion_mode_single_plain_turn(tmp_path):
    from fastapi.testclient import TestClient

    from callforge.presentation.api.app import create_app
    from tests.conftest import make_test_settings

    settings = make_test_settings(tmp_path)
    settings.companion_mode = True
    app = create_app(settings)
    with TestClient(app) as client:
        cid = client.post("/api/v1/conversations/start", json={}).json()[
            "conversation_id"
        ]
        body = client.post(
            f"/api/v1/conversations/{cid}/message",
            json={"content": "Quiero hablar con un humano"},  # would escalate normally
        ).json()
        # Companion mode: no routing/escalation, just a warm plain reply.
        assert body["agent_used"] == "companion"
        assert body["escalated"] is False
        assert body["ticket_id"] is None
        assert body["reply"]
        # One agent run (the single turn), no router/quality/escalation runs
        detail = client.get(f"/api/v1/conversations/{cid}").json()
        assert [m["role"] for m in detail["messages"]] == ["customer", "assistant"]


def test_escalated_conversation_skips_bot_and_keeps_message(client):
    cid = _start(client)
    first = client.post(
        f"/api/v1/conversations/{cid}/message",
        json={"content": "Quiero hablar con un humano"},
    ).json()
    assert first["escalated"] is True
    tickets_before = len(client.get("/api/v1/tickets").json())

    second = client.post(
        f"/api/v1/conversations/{cid}/message",
        json={"content": "Por favor que sea rapido, es urgente"},
    ).json()
    assert second["escalated"] is True
    assert second["agent_used"] == "escalation"
    # companion persona: hands off to "una persona", not a support "humano"
    assert "persona" in second["reply"].lower()
    # no new ticket, no re-run of the bot workflow
    assert len(client.get("/api/v1/tickets").json()) == tickets_before

    detail = client.get(f"/api/v1/conversations/{cid}").json()
    contents = [m["content"] for m in detail["messages"] if m["role"] == "customer"]
    assert "Por favor que sea rapido, es urgente" in contents


def test_list_conversations_with_filter_and_pagination(client):
    ids = [_start(client) for _ in range(3)]
    listed = client.get("/api/v1/conversations").json()
    assert {c["id"] for c in listed} >= set(ids)
    assert all(c["status"] == "active" for c in listed)

    page = client.get("/api/v1/conversations?limit=2").json()
    assert len(page) == 2

    none_escalated = client.get("/api/v1/conversations?status=escalated").json()
    assert none_escalated == []


def test_close_conversation_endpoint(client):
    cid = _start(client)
    result = client.post(
        f"/api/v1/conversations/{cid}/close", json={"resolved": True}
    ).json()
    assert result["status"] == "resolved"

    # CLOSED (not resolved) conversations reject new messages
    cid2 = _start(client)
    client.post(f"/api/v1/conversations/{cid2}/close", json={"resolved": False})
    response = client.post(
        f"/api/v1/conversations/{cid2}/message", json={"content": "hola"}
    )
    assert response.status_code == 409

    assert client.post(
        "/api/v1/conversations/nope/close", json={}
    ).status_code == 404


def test_ticket_status_update(client):
    cid = _start(client)
    body = client.post(
        f"/api/v1/conversations/{cid}/message",
        json={"content": "Quiero hablar con un humano ya"},
    ).json()
    ticket_id = body["ticket_id"]

    updated = client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"status": "in_progress"}
    ).json()
    assert updated["status"] == "in_progress"

    assert client.patch(
        f"/api/v1/tickets/{ticket_id}", json={"status": "no-existe"}
    ).status_code == 422
    assert client.patch(
        "/api/v1/tickets/nope", json={"status": "resolved"}
    ).status_code == 404


def test_webchat_resumes_existing_conversation(client):
    with client.websocket_connect("/webchat/ws") as ws:
        session = ws.receive_json()
        cid = session["conversation_id"]
        assert session["resumed"] is False
        ws.send_text("Mi internet no funciona")
        ws.receive_json()

    # Reconnect with the conversation id -> same conversation, resumed flag
    with client.websocket_connect(f"/webchat/ws?conversation={cid}") as ws:
        session = ws.receive_json()
        assert session["conversation_id"] == cid
        assert session["resumed"] is True

    # Unknown id -> falls back to a fresh conversation
    with client.websocket_connect("/webchat/ws?conversation=nope") as ws:
        session = ws.receive_json()
        assert session["conversation_id"] != "nope"
        assert session["resumed"] is False
