"""E2E por rol (N08): perfiles × rutas × flujos, desde fuera del sistema (HTTP y WebSocket).

Perfiles reales de CallForge:
  · anónimo            — sin credencial.
  · credencial ajena   — una API key que no existe.
  · global             — API_TOKEN (modo de un solo tenant: el tenant «default»).
  · operador de tenant — la API key que el administrador emitió para su empresa.
  · administrador      — X-Admin-Token: sólo da de alta tenants; no es credencial de datos.
  · cliente final      — el webchat con la API key del tenant en la URL.

Se corre igual en proceso (por omisión) o contra la imagen arrancada (CALLFORGE_E2E_URL).
"""
from __future__ import annotations

import pytest

from tests.e2e.conftest import RECHAZOS_WEBCHAT

# Qué puede hacer cada perfil en cada ruta (código HTTP esperado).
RUTAS = {
    "health": ("get", "/api/v1/health", None),
    "metricas": ("get", "/api/v1/metrics", None),
    "iniciar": ("post", "/api/v1/conversations/start", {}),
    "tickets": ("get", "/api/v1/tickets", None),
    "conocimiento": ("post", "/api/v1/knowledge/documents", {"title": "t", "content": "c"}),
    "alta_tenant": ("post", "/api/v1/admin/tenants", {"name": "Empresa de prueba E2E"}),
}
MATRIZ = {
    #               health metricas iniciar tickets conocimiento alta_tenant
    "anonimo":       (200, 401, 401, 401, 401, 401),
    "ajena":         (200, 401, 401, 401, 401, 401),
    "global":        (200, 200, 200, 200, 201, 401),
    "operador":      (200, 200, 200, 200, 201, 401),
    "administrador": (200, 401, 401, 401, 401, 201),
}


def _cabeceras(sistema, perfil: str, clave_operador: str) -> dict[str, str]:
    return {
        "anonimo": {},
        "ajena": {"X-API-Token": "no-es-una-clave"},
        "global": {"X-API-Token": sistema.api_token},
        "operador": {"X-API-Token": clave_operador},
        "administrador": {"X-Admin-Token": sistema.admin_token},
    }[perfil]


def _alta_tenant(sistema, nombre: str) -> str:
    respuesta = sistema.http.post("/api/v1/admin/tenants", json={"name": nombre},
                                  headers={"X-Admin-Token": sistema.admin_token})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["api_key"]


@pytest.mark.parametrize("perfil", list(MATRIZ))
def test_matriz_de_acceso_por_perfil(sistema, perfil):
    clave = _alta_tenant(sistema, "Operador de la matriz")
    obtenidos = []
    for metodo, ruta, cuerpo in RUTAS.values():
        kwargs = {"headers": _cabeceras(sistema, perfil, clave)}
        if cuerpo is not None:
            kwargs["json"] = cuerpo
        obtenidos.append(getattr(sistema.http, metodo)(ruta, **kwargs).status_code)
    assert dict(zip(RUTAS, obtenidos, strict=True)) == dict(zip(RUTAS, MATRIZ[perfil], strict=True))


def test_flujo_completo_de_un_caso_de_soporte(sistema):
    """El administrador da de alta la empresa; su operador carga conocimiento; el cliente reporta una
    falla por el webchat, sigue el diagnóstico y pide un humano; el operador atiende el ticket, cierra
    la conversación y ve sus métricas; el cliente califica."""
    operador = {"X-API-Token": _alta_tenant(sistema, "Acme Soporte")}
    http = sistema.http

    documento = {"title": "Reiniciar el modem", "content": "Desconecta el modem 60 segundos y reconecta.",
                 "tags": ["internet", "modem"]}
    assert http.post("/api/v1/knowledge/documents", json=documento, headers=operador).status_code == 201

    with sistema.webchat(f"token={operador['X-API-Token']}") as ws:
        sesion = ws.receive_json()
        assert sesion["type"] == "session" and sesion["resumed"] is False
        cid = sesion["conversation_id"]
        ws.send_text("Mi internet no funciona desde ayer")
        diagnostico = ws.receive_json()
        assert diagnostico["type"] == "reply" and diagnostico["agent_used"] == "troubleshooting"
        ws.send_text("Reinicié el modem y sigue igual")
        assert ws.receive_json()["agent_used"] == "troubleshooting"
        ws.send_text("Ya no quiero pasos, quiero hablar con un humano")
        escalado = ws.receive_json()
    assert escalado["escalated"] is True and escalado["ticket_id"]

    detalle = http.get(f"/api/v1/conversations/{cid}", headers=operador).json()
    assert detalle["channel"] == "webchat" and detalle["status"] == "escalated"
    assert [p["status"] for p in detalle["resolution_steps"]] == ["answered", "answered"]
    assert detalle["summary"]

    (ticket,) = http.get("/api/v1/tickets", headers=operador).json()
    assert ticket["id"] == escalado["ticket_id"] and ticket["status"] == "open"
    for estado in ("in_progress", "resolved"):
        respuesta = http.patch(f"/api/v1/tickets/{ticket['id']}", json={"status": estado}, headers=operador)
        assert respuesta.json()["status"] == estado
    cierre = http.post(f"/api/v1/conversations/{cid}/close", json={"resolved": True}, headers=operador)
    assert cierre.json()["status"] == "resolved"

    calificacion = http.post("/api/v1/feedback", json={"conversation_id": cid, "rating": 5, "resolved": True},
                             headers=operador)
    assert calificacion.status_code == 201

    metricas = http.get("/api/v1/metrics", headers=operador).json()
    assert metricas["conversations"]["total"] == 1
    assert metricas["conversations"]["by_status"] == {"resolved": 1}
    assert metricas["tickets_total"] == 1 and metricas["escalations_total"] == 1
    assert metricas["feedback"] == {"count": 1, "avg_rating": 5.0}
    assert metricas["agent_errors_total"] == 0


def test_el_cliente_por_rest_recibe_respuesta_y_la_conversacion_reanuda_en_el_webchat(sistema):
    operador = {"X-API-Token": _alta_tenant(sistema, "Acme Facturación")}
    http = sistema.http
    cid = http.post("/api/v1/conversations/start", json={"customer_external_id": "cliente-e2e",
                                                          "channel": "api"}, headers=operador).json()[
        "conversation_id"]
    respuesta = http.post(f"/api/v1/conversations/{cid}/message",
                          json={"content": "Tengo una duda con el cargo de mi factura"}, headers=operador)
    assert respuesta.status_code == 200
    assert respuesta.json()["intent"] == "billing" and respuesta.json()["agent_used"] == "support"

    with sistema.webchat(f"token={operador['X-API-Token']}&conversation={cid}") as ws:
        sesion = ws.receive_json()
    assert sesion["conversation_id"] == cid and sesion["resumed"] is True


def test_el_webchat_rechaza_una_credencial_invalida(sistema):
    with pytest.raises(RECHAZOS_WEBCHAT):   # el servidor cierra (4401) antes de aceptar el socket
        with sistema.webchat("token=no-es-una-clave") as ws:
            ws.receive_json()
