"""Aislamiento multi-tenant: un tenant no ve NI TOCA datos ni métricas de otro.

`test_multi_tenant.py` cubre la lectura (conversación, tickets y métricas de B vacíos). Aquí se
prueba la escritura con la API key equivocada —mensaje, cierre, calificación, ticket— y que nada
de lo intentado cambie los datos ni las métricas del dueño; también la base de conocimiento, el
webchat y que la clave vacía del tenant por omisión no sea una credencial.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from callforge.presentation.api.app import create_app
from tests.conftest import make_test_settings

ADMIN = {"X-Admin-Token": "admin-de-prueba"}


@pytest.fixture
def mt(tmp_path):
    settings = make_test_settings(tmp_path)
    settings.admin_token = ADMIN["X-Admin-Token"]
    with TestClient(create_app(settings)) as client:
        yield client


def _tenant(client, nombre) -> dict[str, str]:
    respuesta = client.post("/api/v1/admin/tenants", json={"name": nombre}, headers=ADMIN)
    assert respuesta.status_code == 201
    return {"X-API-Token": respuesta.json()["api_key"]}


def _conversacion_escalada(client, credencial) -> tuple[str, str]:
    cid = client.post("/api/v1/conversations/start", json={}, headers=credencial).json()["conversation_id"]
    cuerpo = client.post(f"/api/v1/conversations/{cid}/message",
                         json={"content": "Quiero hablar con un humano"}, headers=credencial).json()
    return cid, cuerpo["ticket_id"]


def test_la_api_key_de_un_tenant_no_sirve_para_tocar_datos_de_otro(mt):
    a, b = _tenant(mt, "Empresa A"), _tenant(mt, "Empresa B")
    cid, ticket = _conversacion_escalada(mt, a)
    metricas_antes = mt.get("/api/v1/metrics", headers=a).json()

    intentos = [
        mt.post(f"/api/v1/conversations/{cid}/message", json={"content": "hola"}, headers=b),
        mt.post(f"/api/v1/conversations/{cid}/close", json={"resolved": True}, headers=b),
        mt.post("/api/v1/feedback", json={"conversation_id": cid, "rating": 1, "resolved": True}, headers=b),
        mt.get(f"/api/v1/tickets/{ticket}", headers=b),
        mt.patch(f"/api/v1/tickets/{ticket}", json={"status": "closed"}, headers=b),
    ]
    assert [r.status_code for r in intentos] == [404] * len(intentos)

    # Nada de lo intentado dejó rastro en los datos ni en las métricas de A.
    detalle = mt.get(f"/api/v1/conversations/{cid}", headers=a).json()
    assert detalle["status"] == "escalated"
    assert [m["content"] for m in detalle["messages"] if m["role"] == "customer"] == [
        "Quiero hablar con un humano"]
    assert mt.get(f"/api/v1/tickets/{ticket}", headers=a).json()["status"] == "open"
    assert mt.get("/api/v1/metrics", headers=a).json() == metricas_antes
    metricas_b = mt.get("/api/v1/metrics", headers=b).json()
    assert metricas_b["conversations"]["total"] == 0
    assert metricas_b["agent_runs_total"] == 0 and metricas_b["llm_usage"] == []
    assert metricas_b["feedback"]["count"] == 0


def test_listados_solo_traen_lo_del_tenant(mt):
    a, b = _tenant(mt, "Empresa A"), _tenant(mt, "Empresa B")
    _conversacion_escalada(mt, a)
    assert mt.get("/api/v1/conversations", headers=b).json() == []
    assert len(mt.get("/api/v1/conversations", headers=a).json()) == 1


def test_la_base_de_conocimiento_es_por_tenant(mt):
    """El documento de A no alimenta las respuestas de B: el evento de conocimiento de B sale vacío."""
    a, b = _tenant(mt, "Empresa A"), _tenant(mt, "Empresa B")
    documento = {"title": "Reiniciar el modem", "content": "Desconecta el modem 60 segundos.",
                 "tags": ["internet", "modem"]}
    assert mt.post("/api/v1/knowledge/documents", json=documento, headers=a).status_code == 201

    def aciertos(credencial) -> int:
        from sqlalchemy import text

        cid = mt.post("/api/v1/conversations/start", json={}, headers=credencial).json()["conversation_id"]
        mt.post(f"/api/v1/conversations/{cid}/message", json={"content": "El modem no funciona"},
                headers=credencial)
        with mt.app.state.engine.connect() as conexion:
            carga = conexion.execute(
                text("SELECT payload FROM agent_events WHERE conversation_id = :c "
                     "AND event_type = 'knowledge_retrieved'"), {"c": cid}).scalar_one()
        import json

        return len(json.loads(carga)["hits"])

    assert aciertos(a) == 1
    assert aciertos(b) == 0


def test_el_webchat_no_reanuda_una_conversacion_de_otro_tenant(mt):
    a, b = _tenant(mt, "Empresa A"), _tenant(mt, "Empresa B")
    cid = mt.post("/api/v1/conversations/start", json={}, headers=a).json()["conversation_id"]
    with mt.websocket_connect(f"/webchat/ws?token={b['X-API-Token']}&conversation={cid}") as ws:
        sesion = ws.receive_json()
    assert sesion["resumed"] is False
    assert sesion["conversation_id"] != cid


def test_la_clave_vacia_del_tenant_por_omision_no_es_una_credencial(tmp_path):
    """El tenant «default» se crea con api_key vacía: con API_TOKEN configurado, mandar la
    cabecera vacía no debe abrir la puerta."""
    settings = make_test_settings(tmp_path)
    settings.api_token = "token-global"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/metrics", headers={"X-API-Token": ""}).status_code == 401
        assert client.get("/api/v1/metrics", headers={"X-API-Token": "token-global"}).status_code == 200

    from callforge.infrastructure.database import build_engine, build_session_factory
    from callforge.infrastructure.repositories import SqlTenantRepository

    sesion = build_session_factory(build_engine(settings.database_url))()
    try:
        assert SqlTenantRepository(sesion).get("default").api_key == ""
        assert SqlTenantRepository(sesion).get_by_api_key("") is None
    finally:
        sesion.close()
