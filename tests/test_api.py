from callforge.presentation.api.app import create_app
from fastapi.testclient import TestClient

from tests.conftest import make_test_settings


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm_providers"] == ["mock"]


def test_metrics_after_traffic(client):
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Hola, una pregunta"},
    )
    metrics = client.get("/api/v1/metrics").json()
    assert metrics["conversations"]["total"] == 1
    assert metrics["messages_total"] == 2
    assert metrics["agent_runs_total"] >= 2  # router + specialist (+ quality)
    assert any(u["provider"] == "mock" for u in metrics["llm_usage"])


def test_feedback_marks_conversation_resolved(client):
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Duda de facturacion"},
    )
    response = client.post(
        "/api/v1/feedback",
        json={"conversation_id": conversation_id, "rating": 5, "resolved": True},
    )
    assert response.status_code == 201
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["status"] == "resolved"


def test_api_token_gate(tmp_path):
    settings = make_test_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        denied = client.post("/api/v1/conversations/start", json={})
        assert denied.status_code == 401
        allowed = client.post(
            "/api/v1/conversations/start",
            json={},
            headers={"X-API-Token": "secret-token"},
        )
        assert allowed.status_code == 200
        # health stays open for probes
        assert client.get("/api/v1/health").status_code == 200
