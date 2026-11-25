"""Paridad entre los dos motores del turno: la máquina de estados (SupportWorkflow) y el adaptador LangGraph.

Ambos están detrás del mismo puerto (`application.ports.SupportWorkflowPort`). Cada escenario corre en los
dos con el MISMO guion de LLM (GuionLLM sobre MockProvider) y se compara su HUELLA completa:

* la secuencia de agentes y la decisión/confianza de cada corrida (lo que persiste agent_runs);
* la ENTRADA exacta que recibió cada agente (prompt de usuario: conocimiento, retroalimentación de
  calidad, pasos previos, clasificación del router), así que un contexto distinto también se nota;
* los eventos (tipo, agente y carga, sin el id aleatorio del ticket);
* el resultado: respuesta, agente, intención, categoría, urgencia, confianza, calidad, escalamiento,
  ticket y escalamiento (título, prioridad, motivo, resumen), resumen, respaldo controlado y paso.

El lote golden de punta a punta (7 conversaciones, 12 turnos por la API) corre con los dos motores contra
su oráculo independiente en tests/test_paridad_golden.py, y las reglas del flujo en
tests/test_reglas_flujo.py.

El listón es el CONTROL NEGATIVO: un adaptador mutado (una arista que ya no escala por confianza baja, o
uno que no reintenta) debe dar diferencias EXACTAMENTE en los escenarios que ejercitan esa arista.
"""
from __future__ import annotations

import inspect
import json
import logging
import sys
from pathlib import Path

import pytest
from langgraph.graph import END

from callforge.application.ports import SupportWorkflowPort
from callforge.domain.entities import KnowledgeDocument, ResolutionStep
from callforge.domain.value_objects import ResolutionStepStatus
from callforge.orchestration.langgraph_workflow import NODES, LangGraphSupportWorkflow
from callforge.orchestration.workflow import SupportWorkflow
from tests.apoyo import (
    CAIDO,
    GuionLLM,
    armar_flujo,
    calificar,
    correr,
    enrutar,
    escalamiento,
    responder,
)
from tests.conftest import make_test_settings

DOCUMENTOS = (
    KnowledgeDocument(id="kb-modem", title="Cómo reiniciar el módem",
                      content="Desconecta el módem de la corriente 60 segundos y vuelve a conectarlo.",
                      tags=["internet", "modem"]),
    KnowledgeDocument(id="kb-factura", title="Política de facturación",
                      content="Las facturas se emiten el día 1 de cada mes; se paga hasta el día 10 "
                              "sin recargo.",
                      tags=["facturacion"]),
)


def _previos(n: int) -> list[ResolutionStep]:
    return [ResolutionStep(conversation_id="c-prueba", step_number=i, instruction=f"paso {i}",
                           status=ResolutionStepStatus.ANSWERED, customer_response="sigue igual")
            for i in range(1, n + 1)]


# (id, mensaje, guion por agente, ajustes de la política, pasos previos, con documentos)
ESCENARIOS = [
    ("duda_general", "Hola, ¿qué planes tienen?", {}, {}, 0, True),
    ("facturacion_con_conocimiento", "¿Hasta qué día pago mi factura sin recargo?", {}, {}, 0, True),
    ("tecnico_con_conocimiento", "Mi internet no funciona, el modem parpadea", {}, {}, 0, True),
    ("tecnico_con_pasos_previos", "Sigue sin funcionar el internet", {}, {}, 2, True),
    ("pide_humano", "Quiero hablar con un humano, es urgente", {}, {}, 0, True),
    ("ingles", "Hello, I have a question about my invoice", {}, {}, 0, False),
    ("senales_mezcladas", "No funciona el cobro de mi factura, sale un error", {}, {}, 0, False),
    ("calidad_en_el_umbral", "Hola", {"QualityAgent": [calificar(0.5)]}, {}, 0, False),
    ("reintento_y_mejora", "Hola", {"SupportAgent": [responder("primera"), responder("mejorada")],
                                    "QualityAgent": [calificar(0.2, "no responde"), calificar(0.9)]},
     {}, 0, True),
    ("reintentos_agotados_escala", "Hola", {"QualityAgent": [calificar(0.2), calificar(0.3)]}, {}, 0, False),
    ("sin_reintentos_escala", "Hola", {"QualityAgent": [calificar(0.1)]}, {"reintentos": 0}, 0, False),
    ("dos_reintentos", "Mi internet no funciona",
     {"QualityAgent": [calificar(0.1), calificar(0.2), calificar(0.8)]}, {"reintentos": 2}, 0, True),
    ("quality_caido", "Hola", {"QualityAgent": [CAIDO]}, {}, 0, False),
    ("router_caido", "Hola", {"RouterAgent": [CAIDO]}, {}, 0, False),
    ("especialista_caido", "Hola", {"SupportAgent": [CAIDO]}, {}, 0, False),
    ("reintento_caido", "Hola", {"SupportAgent": [responder(), CAIDO], "QualityAgent": [calificar(0.1)]},
     {}, 0, False),
    ("especialista_sugiere_escalar", "Quiero un reembolso", {"SupportAgent": [responder(escalar=True)]},
     {}, 0, False),
    ("confianza_baja_escala", "Hola", {"SupportAgent": [responder(confianza=0.39)]}, {}, 0, False),
    ("confianza_en_el_umbral", "Hola", {"SupportAgent": [responder(confianza=0.4)]}, {}, 0, False),
    ("agente_fuera_del_catalogo", "Hola", {"RouterAgent": [enrutar("companion")]}, {}, 0, False),
    ("intencion_y_urgencia_invalidas", "Hola", {"RouterAgent": [
        {"intent": "venta", "category": "", "urgency": "altisima", "next_agent": "support",
         "confidence": 0.9}]},
     {}, 0, False),
    ("prioridad_invalida", "Quiero hablar con un humano", {"EscalationAgent": [escalamiento("altisima")]},
     {}, 0, False),
    ("escalation_caido", "Quiero hablar con un humano", {"EscalationAgent": [CAIDO]}, {}, 0, False),
    ("summarizer_caido", "Quiero hablar con un humano", {"SummarizerAgent": [CAIDO]}, {}, 0, False),
    ("respuesta_vacia", "Hola", {"SupportAgent": [responder("   ")]}, {}, 0, False),
    ("paso_nulo", "Mi internet no funciona",
     {"TroubleshootingAgent": [responder("¿Qué modelo es?", paso={"instruction": "null"})]}, {}, 0, False),
    ("support_no_propone_pasos", "Hola", {"SupportAgent": [responder(paso={"instruction": "reinicia"})]},
     {}, 0, False),
    ("tecnico_escala_por_calidad", "El sistema está muy lento",
     {"QualityAgent": [calificar(0.1), calificar(0.1)]}, {}, 1, True),
]
IDS = [e[0] for e in ESCENARIOS]


def huella(resultado, llamadas: list[tuple[str, str]]) -> dict:
    """Todo lo observable de un turno, sin ids aleatorios ni tiempos."""
    def carga(evento):
        datos = json.loads(evento.payload)
        datos.pop("ticket_id", None)
        return datos

    ticket, esc, paso = resultado.ticket, resultado.escalation, resultado.new_resolution_step
    return {
        "corridas": [(r.agent_name.value, r.decision, r.confidence, r.error is not None)
                     for r in resultado.agent_runs],
        "entradas": llamadas,
        "eventos": [(e.event_type.value, e.agent_name.value if e.agent_name else None, carga(e))
                    for e in resultado.events],
        "resultado": {
            "reply": resultado.reply, "agente": resultado.agent_used.value, "intent": resultado.intent.value,
            "categoria": resultado.category, "urgencia": resultado.urgency.value,
            "confianza": resultado.confidence, "calidad": resultado.quality_score,
            "escalada": resultado.escalated, "resumen": resultado.summary,
            "respaldo": resultado.used_controlled_fallback,
            "ticket": (ticket.title, ticket.description, ticket.priority.value) if ticket else None,
            "escalamiento": (esc.reason, esc.priority.value, esc.summary_for_human,
                             esc.ticket_id == ticket.id) if esc else None,
            "paso": (paso.step_number, paso.instruction, paso.expected_check) if paso else None,
        },
    }


def _correr_escenario(escenario, motor: str, clase=None):
    _, mensaje, guion, ajustes, previos, con_documentos = escenario
    llm = GuionLLM(guion)
    flujo = armar_flujo(llm, motor=motor, clase=clase, **ajustes)
    documentos = DOCUMENTOS if con_documentos else ()
    resultado = correr(flujo, mensaje, pasos=_previos(previos), documentos=documentos)
    return resultado, huella(resultado, llm.llamadas)


def diferencias(clase_langgraph=LangGraphSupportWorkflow) -> dict[str, list[str]]:
    """Escenario -> secciones de la huella que difieren entre la máquina de estados y el adaptador."""
    distintos = {}
    for escenario in ESCENARIOS:
        _, esperado = _correr_escenario(escenario, "state_machine")
        _, obtenido = _correr_escenario(escenario, "langgraph", clase_langgraph)
        secciones = [s for s in esperado if esperado[s] != obtenido[s]]
        if secciones:
            distintos[escenario[0]] = secciones
    return distintos


# --------------------------------------------------------------------------- #
# Paridad
# --------------------------------------------------------------------------- #


def test_los_dos_motores_implementan_el_mismo_puerto():
    """Misma firma que el puerto de aplicación: el caso de uso no sabe qué motor tiene detrás."""
    firma = inspect.signature(SupportWorkflowPort.handle_message)
    for clase in (SupportWorkflow, LangGraphSupportWorkflow):
        assert inspect.signature(clase.handle_message) == firma
        assert inspect.iscoroutinefunction(clase.handle_message)
        assert type(armar_flujo(GuionLLM(), motor="langgraph" if clase is LangGraphSupportWorkflow
                                else "state_machine")) is clase


@pytest.mark.parametrize("escenario", ESCENARIOS, ids=IDS)
def test_paridad_de_la_huella_por_escenario(escenario):
    _, esperado = _correr_escenario(escenario, "state_machine")
    _, obtenido = _correr_escenario(escenario, "langgraph")
    assert obtenido == esperado


def test_paridad_del_conjunto_completo_y_cobertura_de_ramas():
    """Sin diferencias en ningún escenario, y el conjunto recorre todas las ramas del grafo."""
    assert diferencias() == {}
    visitados = set()
    for escenario in ESCENARIOS:
        resultado, _ = _correr_escenario(escenario, "langgraph")
        visitados.update(t.node for t in resultado.node_traces)
        visitados.update((t.node, t.next) for t in resultado.node_traces)
    assert set(NODES) <= visitados
    aristas = {v for v in visitados if isinstance(v, tuple)}
    assert aristas == {
        ("router", "retrieve"), ("router", "escalation"), ("router", "fallback"),
        ("retrieve", "support"), ("retrieve", "troubleshooting"),
        ("support", "quality"), ("support", "fallback"), ("troubleshooting", "quality"),
        ("quality", "decide"), ("quality", "support"), ("quality", "troubleshooting"),
        ("decide", "__end__"), ("decide", "escalation"),
        ("escalation", "summarizer"), ("summarizer", "__end__"), ("fallback", "__end__"),
    }


# --------------------------------------------------------------------------- #
# Control negativo: un adaptador mutado DEBE dar diferencias, y sólo donde se mutó
# --------------------------------------------------------------------------- #


class SinEscalarPorConfianza(LangGraphSupportWorkflow):
    """Mutación: la arista decide → escalation ya no mira la confianza del especialista."""

    async def _decide(self, state, runtime):
        respuesta = state["reply"]
        if self._policy.should_escalate(
            router_next_agent=state["next_agent"],
            agent_suggested=bool(respuesta.data.get("suggest_escalation", False)),
            agent_confidence=1.0,
            quality_score=state.get("quality_score"),
            retries_exhausted=state.get("retries", 0) >= self._policy.max_quality_retries,
        ):
            return {"escalated_by": self._specialists[state["next_agent"]].name, "route": "escalation"}
        return {"route": END}


class SinReintentos(LangGraphSupportWorkflow):
    """Mutación: la arista quality → especialista (reintento) desaparece."""

    async def _check_quality(self, state, runtime):
        actualizacion = await super()._check_quality(state, runtime)
        actualizacion["route"] = "decide"
        eventos = actualizacion.get("events", [])
        actualizacion["events"] = [e for e in eventos if e.event_type.value != "retried"]
        actualizacion.pop("retries", None)
        actualizacion.pop("quality_feedback", None)
        return actualizacion


def test_control_negativo_sin_escalar_por_confianza_difiere_solo_donde_la_confianza_es_baja():
    distintos = diferencias(SinEscalarPorConfianza)
    assert sorted(distintos) == ["confianza_baja_escala"]
    assert set(distintos["confianza_baja_escala"]) >= {"corridas", "resultado"}


def test_control_negativo_sin_reintentos_difiere_exactamente_en_los_escenarios_con_reintento():
    distintos = diferencias(SinReintentos)
    assert sorted(distintos) == sorted([
        "dos_reintentos", "reintento_caido", "reintento_y_mejora", "reintentos_agotados_escala",
        "tecnico_escala_por_calidad"])


# --------------------------------------------------------------------------- #
# Trazas por nodo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("escenario", ESCENARIOS, ids=IDS)
def test_las_trazas_por_nodo_cuentan_el_mismo_recorrido_que_las_corridas(escenario):
    resultado, _ = _correr_escenario(escenario, "langgraph")
    trazas = resultado.node_traces
    assert trazas[0].node == "router"
    assert [a for t in trazas for a in t.agents] == [r.agent_name.value for r in resultado.agent_runs]
    for actual, siguiente in zip(trazas, trazas[1:], strict=False):
        assert actual.next == siguiente.node          # cada traza dice a dónde fue, y allá fue
    assert trazas[-1].next == "__end__"
    assert all(t.latency_ms >= 0 for t in trazas)


def test_cada_nodo_deja_una_linea_de_bitacora(caplog):
    caplog.set_level(logging.INFO, logger="callforge.orchestration.langgraph_workflow")
    resultado, _ = _correr_escenario(ESCENARIOS[2], "langgraph")
    lineas = [r.getMessage() for r in caplog.records if r.getMessage().startswith("langgraph nodo=")]
    assert [linea.split()[1] for linea in lineas] == [f"nodo={t.node}" for t in resultado.node_traces]


def test_la_maquina_de_estados_no_deja_trazas_de_grafo():
    resultado, _ = _correr_escenario(ESCENARIOS[0], "state_machine")
    assert resultado.node_traces == []


def test_el_diagrama_del_adr_es_el_grafo_compilado():
    """docs/adr/0005 muestra el grafo generado del código; si cambia una arista, el diagrama debe cambiar."""
    adr = (Path(__file__).resolve().parents[1] / "docs" / "adr" / "0005-adaptador-langgraph.md").read_text(
        encoding="utf-8")
    bloque = adr.split("<!-- grafo:inicio -->")[1].split("<!-- grafo:fin -->")[0].strip()
    generado = armar_flujo(GuionLLM(), motor="langgraph").graph.get_graph().draw_mermaid().rstrip("\n")
    assert bloque == f"```mermaid\n{generado}\n```"


# --------------------------------------------------------------------------- #
# Selección del motor (WORKFLOW_ENGINE)
# --------------------------------------------------------------------------- #


def test_workflow_engine_elige_el_motor_y_la_omision_es_la_maquina_de_estados(tmp_path):
    from callforge.config import Settings
    from callforge.presentation.api.app import create_app

    assert Settings.model_fields["workflow_engine"].default == "state_machine"
    assert type(create_app(make_test_settings(tmp_path)).state.workflow) is SupportWorkflow
    ajustes = make_test_settings(tmp_path).model_copy(update={"workflow_engine": "langgraph"})
    assert type(create_app(ajustes).state.workflow) is LangGraphSupportWorkflow


def test_un_motor_desconocido_no_arranca():
    from pydantic import ValidationError

    from callforge.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, workflow_engine="crewai")


def test_sin_langgraph_instalado_el_motor_langgraph_falla_con_un_mensaje_claro(tmp_path, monkeypatch):
    """La imagen de producción no trae el framework: pedirlo sin instalarlo es un error al arrancar."""
    from callforge.infrastructure.llm.mock_provider import MockProvider
    from callforge.orchestration.factory import build_support_workflow

    monkeypatch.setitem(sys.modules, "langgraph", None)
    monkeypatch.setitem(sys.modules, "langgraph.graph", None)
    monkeypatch.delitem(sys.modules, "callforge.orchestration.langgraph_workflow")
    ajustes = make_test_settings(tmp_path).model_copy(update={"workflow_engine": "langgraph"})
    with pytest.raises(RuntimeError, match="requirements-langgraph.txt"):
        build_support_workflow(ajustes, MockProvider())
