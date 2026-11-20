"""Reglas del SupportWorkflow (la máquina de estados del turno), una prueba por regla.

Un turno recorre: Router → (conocimiento) → especialista → calidad → [reintento] → política de
escalamiento → [Escalation + Summarizer]. Cada prueba fija qué contesta cada agente (GuionLLM) y
afirma la RUTA (agentes que corrieron, en orden), los eventos y el resultado. Las transiciones
inválidas (un agente que el router no puede elegir, prioridades o urgencias fuera del catálogo,
un proveedor caído a mitad del turno) se prueban igual que las válidas.
"""
from __future__ import annotations

import json

import pytest

from callforge.domain.entities import ResolutionStep
from callforge.domain.value_objects import ResolutionStepStatus
from callforge.infrastructure.voice.phrases import CONTROLLED_FALLBACK
from tests.apoyo import (
    CAIDO,
    GuionLLM,
    armar_flujo,
    calificar,
    correr,
    enrutar,
    escalamiento,
    eventos,
    responder,
    ruta,
)

# --------------------------------------------------------------------------- #
# Enrutamiento por intención y urgencia
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mensaje", "intent", "siguiente"),
    [
        ("Quiero hablar con un humano, mi internet no funciona", "human_request", "escalation"),
        ("No funciona el cobro de mi factura, sale un error", "technical_issue", "troubleshooting"),
        ("Tengo una duda con el cargo de mi factura", "billing", "support"),
        ("Hola, ¿qué planes tienen?", "question", "support"),
    ],
)
def test_precedencia_del_enrutamiento_humano_tecnico_facturacion(mensaje, intent, siguiente):
    """Con señales mezcladas manda la más fuerte: humano > falla técnica > facturación > duda."""
    llm = GuionLLM()
    resultado = correr(armar_flujo(llm), mensaje)
    assert resultado.intent.value == intent
    primera = json.loads(resultado.events[0].payload)
    assert primera["next_agent"] == siguiente


def test_el_router_no_puede_elegir_un_agente_fuera_del_catalogo():
    """Transición inválida: next_agent desconocido (o del modo retirado) se trata como «support»."""
    for invalido in ("companion", "billing_agent", None, 42):
        llm = GuionLLM({"RouterAgent": [enrutar(invalido)]})
        resultado = correr(armar_flujo(llm), "Hola")
        assert resultado.agent_used.value == "support"
        assert ruta(resultado) == ["router", "support", "quality"]


def test_intencion_y_urgencia_invalidas_caen_a_other_y_medium():
    llm = GuionLLM({"RouterAgent": [
        {"intent": "venta_cruzada", "category": "", "urgency": "altisima", "next_agent": "support",
         "confidence": 0.9},
    ]})
    resultado = correr(armar_flujo(llm), "Hola")
    assert resultado.intent.value == "other"
    assert resultado.urgency.value == "medium"
    assert resultado.category == "general"


def test_la_urgencia_del_router_viaja_al_resultado():
    llm = GuionLLM({"RouterAgent": [enrutar("support", intent="complaint", urgencia="critical",
                                            categoria="caida")]})
    resultado = correr(armar_flujo(llm), "Todo el servicio está caído")
    assert (resultado.intent.value, resultado.urgency.value, resultado.category) == (
        "complaint", "critical", "caida")


def test_el_conocimiento_se_consulta_solo_para_support_y_troubleshooting():
    tecnico = correr(armar_flujo(GuionLLM()), "Mi internet no funciona")
    escalado = correr(armar_flujo(GuionLLM()), "Quiero hablar con un humano")
    assert "knowledge_retrieved" in eventos(tecnico)
    assert "knowledge_retrieved" not in eventos(escalado)


# --------------------------------------------------------------------------- #
# Calidad: reintento por baja calificación del QualityAgent
# --------------------------------------------------------------------------- #


def test_calidad_suficiente_no_reintenta():
    llm = GuionLLM({"QualityAgent": [calificar(0.5)]})  # en el umbral: no es «baja»
    resultado = correr(armar_flujo(llm, umbral_calidad=0.5), "Hola")
    assert ruta(resultado) == ["router", "support", "quality"]
    assert "retried" not in eventos(resultado)
    assert resultado.quality_score == 0.5
    assert resultado.escalated is False


def test_calidad_baja_reintenta_una_vez_con_la_retroalimentacion():
    llm = GuionLLM({
        "SupportAgent": [responder("primera"), responder("mejorada")],
        "QualityAgent": [calificar(0.2, "no responde lo preguntado"), calificar(0.9)],
    })
    resultado = correr(armar_flujo(llm), "Hola")
    assert ruta(resultado) == ["router", "support", "quality", "support", "quality"]
    assert eventos(resultado).count("retried") == 1
    assert resultado.reply == "mejorada"
    assert resultado.quality_score == 0.9
    assert resultado.escalated is False
    segunda_entrada = [e for a, e in llm.llamadas if a == "SupportAgent"][1]
    assert "PREVIOUS ATTEMPT WAS REJECTED BY QUALITY REVIEW" in segunda_entrada
    assert "no responde lo preguntado" in segunda_entrada


def test_calidad_baja_tras_agotar_reintentos_escala():
    llm = GuionLLM({"QualityAgent": [calificar(0.2), calificar(0.3)]})
    resultado = correr(armar_flujo(llm, reintentos=1), "Hola")
    assert ruta(resultado) == [
        "router", "support", "quality", "support", "quality", "escalation", "summarizer"]
    assert resultado.escalated is True
    assert resultado.ticket is not None
    assert resultado.agent_used.value == "support"   # quien no logró la calidad


def test_sin_reintentos_configurados_la_calidad_baja_escala_de_inmediato():
    llm = GuionLLM({"QualityAgent": [calificar(0.1)]})
    resultado = correr(armar_flujo(llm, reintentos=0), "Hola")
    assert ruta(resultado) == ["router", "support", "quality", "escalation", "summarizer"]
    assert resultado.escalated is True


def test_si_el_quality_agent_falla_no_se_reintenta_y_la_respuesta_sale():
    llm = GuionLLM({"QualityAgent": [CAIDO]})
    resultado = correr(armar_flujo(llm), "Hola")
    assert ruta(resultado) == ["router", "support", "quality"]
    assert resultado.quality_score is None
    assert resultado.escalated is False
    assert resultado.reply == json.loads(llm._mock._respond("SupportAgent", "Hola"))["reply"]


# --------------------------------------------------------------------------- #
# Escalamiento
# --------------------------------------------------------------------------- #


def test_el_router_escala_directo_sin_especialista_ni_calidad():
    resultado = correr(armar_flujo(GuionLLM()), "Quiero hablar con un humano")
    assert ruta(resultado) == ["router", "escalation", "summarizer"]
    assert resultado.escalated is True
    assert resultado.agent_used.value == "router"
    assert resultado.quality_score is None
    assert resultado.ticket.title.startswith("[escalation] Quiero hablar con un humano")
    assert resultado.escalation.ticket_id == resultado.ticket.id
    assert "escalated" in eventos(resultado)


def test_el_especialista_que_sugiere_escalar_escala():
    llm = GuionLLM({"SupportAgent": [responder(escalar=True)]})
    resultado = correr(armar_flujo(llm), "Quiero un reembolso")
    assert ruta(resultado) == ["router", "support", "quality", "escalation", "summarizer"]
    assert resultado.escalated is True


def test_confianza_baja_del_especialista_escala():
    llm = GuionLLM({"SupportAgent": [responder(confianza=0.39)]})
    resultado = correr(armar_flujo(llm, umbral_confianza=0.4), "Hola")
    assert resultado.escalated is True


def test_confianza_en_el_umbral_no_escala():
    llm = GuionLLM({"SupportAgent": [responder(confianza=0.4)]})
    resultado = correr(armar_flujo(llm, umbral_confianza=0.4), "Hola")
    assert resultado.escalated is False


@pytest.mark.parametrize(("propuesta", "esperada"),
                         [("urgent", "urgent"), ("low", "low"), ("altisima", "medium"), (None, "medium")])
def test_la_prioridad_del_ticket_sale_del_escalamiento_y_la_invalida_cae_a_medium(propuesta, esperada):
    llm = GuionLLM({"EscalationAgent": [escalamiento(propuesta)]})
    resultado = correr(armar_flujo(llm), "Quiero hablar con un humano")
    assert resultado.ticket.priority.value == esperada
    assert resultado.escalation.priority.value == esperada


def test_el_resumen_del_summarizer_queda_en_el_resultado_del_escalamiento():
    resumen = {"problem": "sin internet", "actions_taken": ["reinicio"], "final_status": "escalated",
               "next_steps": ["visita técnica"]}
    llm = GuionLLM({"SummarizerAgent": [resumen]})
    resultado = correr(armar_flujo(llm), "Quiero hablar con un humano")
    assert json.loads(resultado.summary) == resumen


# --------------------------------------------------------------------------- #
# Diagnóstico guiado (pasos de resolución)
# --------------------------------------------------------------------------- #


def test_troubleshooting_numera_el_paso_despues_de_los_previos():
    previos = [ResolutionStep(conversation_id="c-prueba", step_number=n, instruction=f"paso {n}",
                              status=ResolutionStepStatus.ANSWERED) for n in (1, 2)]
    resultado = correr(armar_flujo(GuionLLM()), "Sigue sin funcionar el internet", pasos=previos)
    assert resultado.new_resolution_step.step_number == 3


@pytest.mark.parametrize("instruccion", ["", "null", "  ", None])
def test_un_paso_sin_instruccion_no_se_registra(instruccion):
    llm = GuionLLM({"TroubleshootingAgent": [
        responder("¿Me confirmas el modelo del equipo?", paso={"instruction": instruccion}),
    ]})
    resultado = correr(armar_flujo(llm), "Mi internet no funciona")
    assert resultado.agent_used.value == "troubleshooting"
    assert resultado.new_resolution_step is None


def test_support_nunca_propone_pasos_de_diagnostico():
    llm = GuionLLM({"SupportAgent": [responder(paso={"instruction": "reinicia"})]})
    resultado = correr(armar_flujo(llm), "Hola")
    assert resultado.new_resolution_step is None


# --------------------------------------------------------------------------- #
# Proveedor caído a mitad del turno: respuesta controlada, nunca una excepción
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("guion", "ruta_esperada"),
    [
        ({"RouterAgent": [CAIDO]}, ["router"]),
        ({"SupportAgent": [CAIDO]}, ["router", "support"]),
        ({"SupportAgent": [responder(), CAIDO], "QualityAgent": [calificar(0.1)]},
         ["router", "support", "quality", "support"]),
    ],
    ids=["router", "especialista", "reintento"],
)
def test_un_fallo_de_llm_en_cualquier_paso_da_la_respuesta_controlada(guion, ruta_esperada):
    resultado = correr(armar_flujo(GuionLLM(guion)), "Hola")
    assert resultado.reply == CONTROLLED_FALLBACK
    assert resultado.used_controlled_fallback is True
    assert resultado.escalated is False and resultado.ticket is None
    assert resultado.confidence == 0.0
    assert ruta(resultado) == ruta_esperada
    assert resultado.agent_runs[-1].error  # el fallo queda trazado en la corrida
    assert eventos(resultado)[-1] == "fallback_used"


def test_una_respuesta_vacia_del_especialista_usa_la_respuesta_controlada():
    llm = GuionLLM({"SupportAgent": [responder("   ")]})
    resultado = correr(armar_flujo(llm), "Hola")
    assert resultado.reply == CONTROLLED_FALLBACK
