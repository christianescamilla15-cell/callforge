"""Ciclo de vida de la conversación (máquina de estados), con sus transiciones INVÁLIDAS.

La tabla vive en `callforge/domain/lifecycle.py`; aquí se prueba completa (todas las parejas de
estados) y, por la API, cada operación que mueve el estado: mensaje, escalamiento, calificación y
cierre. Ver docs/adr/0003-ciclo-de-vida-de-la-conversacion.md.
"""
from __future__ import annotations

import itertools

import pytest

from callforge.domain.entities import Conversation
from callforge.domain.lifecycle import InvalidTransitionError, can_transition, transition
from callforge.domain.value_objects import ConversationStatus as S

PERMITIDAS = {
    (S.ACTIVE, S.ACTIVE), (S.ACTIVE, S.ESCALATED), (S.ACTIVE, S.RESOLVED), (S.ACTIVE, S.CLOSED),
    (S.RESOLVED, S.RESOLVED), (S.RESOLVED, S.ACTIVE), (S.RESOLVED, S.CLOSED),
    (S.ESCALATED, S.ESCALATED), (S.ESCALATED, S.RESOLVED), (S.ESCALATED, S.CLOSED),
}


@pytest.mark.parametrize(("origen", "destino"), list(itertools.product(S, S)),
                         ids=lambda s: s.value)
def test_tabla_completa_de_transiciones(origen, destino):
    """Las 16 parejas: 10 permitidas; inválidas ESCALATED→ACTIVE, RESOLVED→ESCALATED y todo desde CLOSED."""
    conversacion = Conversation(status=origen)
    if (origen, destino) in PERMITIDAS:
        assert can_transition(origen, destino)
        transition(conversacion, destino)
        assert conversacion.status == destino
    else:
        assert not can_transition(origen, destino)
        with pytest.raises(InvalidTransitionError):
            transition(conversacion, destino)
        assert conversacion.status == origen  # una transición rechazada no deja rastro


def test_una_transicion_real_actualiza_la_fecha_y_la_misma_no():
    conversacion = Conversation(status=S.ACTIVE)
    antes = conversacion.updated_at
    transition(conversacion, S.ACTIVE)
    assert conversacion.updated_at == antes
    transition(conversacion, S.RESOLVED)
    assert conversacion.updated_at > antes


# --------------------------------------------------------------------------- #
# Por la API
# --------------------------------------------------------------------------- #


def _iniciar(client) -> str:
    return client.post("/api/v1/conversations/start", json={}).json()["conversation_id"]


def _estado(client, cid) -> str:
    return client.get(f"/api/v1/conversations/{cid}").json()["status"]


def _mensaje(client, cid, texto):
    return client.post(f"/api/v1/conversations/{cid}/message", json={"content": texto})


def _cerrar(client, cid, resuelta):
    return client.post(f"/api/v1/conversations/{cid}/close", json={"resolved": resuelta})


def _calificar(client, cid, resuelta=True):
    return client.post("/api/v1/feedback", json={"conversation_id": cid, "rating": 4, "resolved": resuelta})


def test_una_conversacion_nueva_empieza_activa(client):
    assert _estado(client, _iniciar(client)) == "active"


def test_cerrada_es_terminal_no_se_reabre_ni_se_cierra_otra_vez(client):
    cid = _iniciar(client)
    assert _cerrar(client, cid, False).json()["status"] == "closed"
    assert _cerrar(client, cid, True).status_code == 409   # no se «resuelve» después de cerrada
    assert _cerrar(client, cid, False).status_code == 409
    assert _mensaje(client, cid, "hola").status_code == 409
    assert _calificar(client, cid).status_code == 201       # la calificación sí se registra…
    assert _estado(client, cid) == "closed"                  # …pero no mueve el estado


def test_escalada_no_vuelve_al_bot_ni_la_resuelve_la_calificacion(client):
    cid = _iniciar(client)
    assert _mensaje(client, cid, "Quiero hablar con un humano").json()["escalated"] is True
    assert _calificar(client, cid).status_code == 201
    assert _estado(client, cid) == "escalated"
    otra = _mensaje(client, cid, "¿Siguen ahí?").json()
    assert otra["agent_used"] == "escalation" and otra["conversation_status"] == "escalated"


def test_el_agente_humano_cierra_la_escalada_como_resuelta(client):
    cid = _iniciar(client)
    _mensaje(client, cid, "Quiero hablar con un humano")
    assert _cerrar(client, cid, True).json()["status"] == "resolved"


def test_resuelta_se_reabre_si_el_cliente_vuelve_a_escribir(client):
    cid = _iniciar(client)
    _mensaje(client, cid, "Tengo una duda con mi factura")
    _calificar(client, cid)
    assert _estado(client, cid) == "resolved"
    respuesta = _mensaje(client, cid, "Otra pregunta: ¿cómo cambio mi plan?").json()
    assert respuesta["conversation_status"] == "active"
    assert respuesta["agent_used"] == "support"   # el bot atiende la conversación reabierta


def test_resuelta_que_se_reabre_y_pide_humano_queda_escalada(client):
    cid = _iniciar(client)
    _cerrar(client, cid, True)
    respuesta = _mensaje(client, cid, "Quiero hablar con un humano").json()
    assert respuesta["conversation_status"] == "escalated"


def test_resuelta_puede_archivarse_como_cerrada(client):
    cid = _iniciar(client)
    _cerrar(client, cid, True)
    assert _cerrar(client, cid, True).json()["status"] == "resolved"   # idempotente
    assert _cerrar(client, cid, False).json()["status"] == "closed"


def test_calificar_sin_resolver_no_cambia_el_estado(client):
    cid = _iniciar(client)
    _mensaje(client, cid, "Hola")
    assert _calificar(client, cid, resuelta=False).status_code == 201
    assert _estado(client, cid) == "active"


def test_operar_sobre_una_conversacion_inexistente_da_404(client):
    assert _mensaje(client, "no-existe", "hola").status_code == 404
    assert _cerrar(client, "no-existe", True).status_code == 404
    assert _calificar(client, "no-existe").status_code == 404
    assert client.get("/api/v1/conversations/no-existe").status_code == 404
