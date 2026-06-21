from fastapi.testclient import TestClient

from callforge.presentation.api.app import create_app

from tests.conftest import make_test_settings


def test_webchat_page_serves_html(client):
    response = client.get("/webchat")
    assert response.status_code == 200
    assert "CallForge" in response.text


def test_dashboard_page_serves_html(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Dashboard" in response.text or "dashboard" in response.text


def test_webchat_full_conversation(client):
    with client.websocket_connect("/webchat/ws") as ws:
        session = ws.receive_json()
        assert session["type"] == "session"
        conversation_id = session["conversation_id"]

        ws.send_text("Mi internet no funciona desde ayer")
        reply = ws.receive_json()
        assert reply["type"] == "reply"
        assert reply["agent_used"] == "troubleshooting"
        assert reply["reply"]

    # The webchat conversation is queryable through the REST API too
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["channel"] == "webchat"
    assert len(detail["messages"]) == 2


def test_webchat_requires_token_when_configured(tmp_path):
    settings = make_test_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        # Wrong/missing token -> closed with policy code before accept
        try:
            with client.websocket_connect("/webchat/ws") as ws:
                ws.receive_json()
            connected = True
        except Exception:
            connected = False
        assert not connected

        with client.websocket_connect("/webchat/ws?token=secret-token") as ws:
            assert ws.receive_json()["type"] == "session"
