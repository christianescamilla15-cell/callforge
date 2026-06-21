import pytest
from fastapi.testclient import TestClient

from callforge.presentation.api.app import create_app

from tests.conftest import make_test_settings

ADMIN = {"X-Admin-Token": "admin-secret"}


@pytest.fixture
def mt_client(tmp_path):
    settings = make_test_settings(tmp_path)
    settings.admin_token = "admin-secret"
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


def _create_tenant(client, name) -> dict:
    response = client.post("/api/v1/admin/tenants", json={"name": name}, headers=ADMIN)
    assert response.status_code == 201
    return response.json()


def test_admin_api_disabled_without_admin_token(client):
    response = client.post("/api/v1/admin/tenants", json={"name": "X"})
    assert response.status_code == 403


def test_admin_requires_correct_token(mt_client):
    response = mt_client.post(
        "/api/v1/admin/tenants", json={"name": "X"}, headers={"X-Admin-Token": "wrong"}
    )
    assert response.status_code == 401


def test_unknown_api_token_is_rejected(mt_client):
    response = mt_client.post(
        "/api/v1/conversations/start", json={}, headers={"X-API-Token": "garbage"}
    )
    assert response.status_code == 401


def test_tenants_are_isolated(mt_client):
    tenant_a = _create_tenant(mt_client, "Empresa A")
    tenant_b = _create_tenant(mt_client, "Empresa B")
    headers_a = {"X-API-Token": tenant_a["api_key"]}
    headers_b = {"X-API-Token": tenant_b["api_key"]}

    # Tenant A creates a conversation and escalates -> ticket
    conversation_id = mt_client.post(
        "/api/v1/conversations/start", json={}, headers=headers_a
    ).json()["conversation_id"]
    body = mt_client.post(
        f"/api/v1/conversations/{conversation_id}/message",
        json={"content": "Quiero hablar con un humano"},
        headers=headers_a,
    ).json()
    assert body["escalated"] is True

    # Tenant B cannot see A's conversation nor its ticket
    assert (
        mt_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=headers_b
        ).status_code
        == 404
    )
    assert mt_client.get("/api/v1/tickets", headers=headers_b).json() == []
    assert len(mt_client.get("/api/v1/tickets", headers=headers_a).json()) == 1

    # Metrics are scoped
    metrics_a = mt_client.get("/api/v1/metrics", headers=headers_a).json()
    metrics_b = mt_client.get("/api/v1/metrics", headers=headers_b).json()
    assert metrics_a["conversations"]["total"] == 1
    assert metrics_b["conversations"]["total"] == 0
    assert metrics_a["tenant_id"] == tenant_a["id"]

    # Same external customer id is a DIFFERENT customer per tenant
    first = mt_client.post(
        "/api/v1/conversations/start",
        json={"customer_external_id": "shared-id"},
        headers=headers_a,
    ).json()
    second = mt_client.post(
        "/api/v1/conversations/start",
        json={"customer_external_id": "shared-id"},
        headers=headers_b,
    ).json()
    assert first["customer_id"] != second["customer_id"]


def test_default_tenant_open_mode_still_works(client):
    # No tokens configured anywhere -> everything lands on the default tenant
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    assert (
        client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
    )
