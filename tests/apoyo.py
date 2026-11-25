"""Apoyo de pruebas: un proveedor LLM con guion por agente y un SupportWorkflow armado con él.

El MockProvider responde siempre lo mismo por agente; para probar las ramas del flujo (calidad
baja, reintento, prioridad inválida, fallo a mitad del turno) hace falta decidir qué contesta
cada agente en cada llamada. `GuionLLM` lo hace y registra cada llamada para poder afirmar QUÉ
agentes corrieron, en qué orden y con qué entrada.
"""
from __future__ import annotations

import asyncio
import json

from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.domain.entities import ResolutionStep
from callforge.infrastructure.knowledge.store import KeywordKnowledgeStore
from callforge.infrastructure.llm.base import LLMProvider, LLMResult, LLMUnavailableError
from callforge.infrastructure.llm.mock_provider import MockProvider
from callforge.orchestration.policies import EscalationPolicy
from callforge.orchestration.workflow import SupportWorkflow, WorkflowOutcome

# Motor con el que `armar_flujo` arma el turno. Las pruebas de reglas del flujo lo parametrizan para que
# cada regla se cumpla con la máquina de estados Y con el adaptador LangGraph (tests/test_reglas_flujo.py).
MOTOR = "state_machine"

AGENTES = (
    "RouterAgent", "TroubleshootingAgent", "EscalationAgent", "SummarizerAgent",
    "QualityAgent", "SupportAgent",
)

CAIDO = LLMUnavailableError("todos los proveedores fallaron (simulado)")


class GuionLLM(LLMProvider):
    """Responde por agente según una cola de respuestas; sin guion, contesta el MockProvider.

    Cada elemento de la cola es un dict (se devuelve como JSON) o una excepción (se lanza).
    """

    name = "guion"

    def __init__(self, guion: dict[str, list] | None = None) -> None:
        self._colas = {agente: list(respuestas) for agente, respuestas in (guion or {}).items()}
        self._mock = MockProvider()
        self.llamadas: list[tuple[str, str]] = []

    async def complete(self, system, messages, json_mode=False) -> LLMResult:
        agente = next((a for a in AGENTES if a in system), "?")
        entrada = messages[-1]["content"] if messages else ""
        self.llamadas.append((agente, entrada))
        cola = self._colas.get(agente)
        if cola:
            respuesta = cola.pop(0)
            if isinstance(respuesta, Exception):
                raise respuesta
            texto = json.dumps(respuesta, ensure_ascii=False)
            return LLMResult(text=texto, provider=self.name, model="guion",
                             tokens_in=len(entrada.split()), tokens_out=len(texto.split()))
        return await self._mock.complete(system, messages, json_mode=json_mode)

    def agentes(self) -> list[str]:
        return [agente for agente, _ in self.llamadas]


class RepoConocimientoEnMemoria:
    def __init__(self, documentos=()) -> None:
        self.documentos = list(documentos)

    def add(self, documento) -> None:
        self.documentos.append(documento)

    def list_all(self):
        return list(self.documentos)

    def update_embedding(self, document_id, embedding) -> None:  # pragma: no cover - sin embeddings
        pass


def armar_flujo(llm: LLMProvider, *, umbral_calidad: float = 0.5, umbral_confianza: float = 0.4,
                reintentos: int = 1, motor: str | None = None, clase=None):
    """El turno con `motor` (o el de MOTOR); `clase` arma en su lugar una variante (controles negativos)."""
    if clase is None:
        clase = SupportWorkflow
        if (motor or MOTOR) == "langgraph":
            from callforge.orchestration.langgraph_workflow import LangGraphSupportWorkflow as clase
    return clase(
        router=RouterAgent(llm),
        support=SupportAgent(llm),
        troubleshooting=TroubleshootingAgent(llm),
        escalation=EscalationAgent(llm),
        summarizer=SummarizerAgent(llm),
        quality=QualityAgent(llm),
        policy=EscalationPolicy(quality_threshold=umbral_calidad,
                                confidence_threshold=umbral_confianza,
                                max_quality_retries=reintentos),
    )


def correr(flujo, mensaje: str, *, historia: list[dict] | None = None,
           pasos: list[ResolutionStep] | None = None, documentos=()) -> WorkflowOutcome:
    return asyncio.run(flujo.handle_message(
        conversation_id="c-prueba",
        user_message=mensaje,
        history=historia or [],
        knowledge_store=KeywordKnowledgeStore(RepoConocimientoEnMemoria(documentos)),
        resolution_steps=pasos,
    ))


def ruta(resultado: WorkflowOutcome) -> list[str]:
    """Los agentes que corrieron en el turno, en orden (lo que persiste agent_runs)."""
    return [r.agent_name.value for r in resultado.agent_runs]


def eventos(resultado: WorkflowOutcome) -> list[str]:
    return [e.event_type.value for e in resultado.events]


# Respuestas típicas de cada agente, para armar guiones legibles.
def enrutar(siguiente: str, *, intent: str = "question", urgencia: str = "low",
            categoria: str = "general", confianza: float = 0.9) -> dict:
    return {"intent": intent, "category": categoria, "urgency": urgencia, "frustration": "low",
            "next_agent": siguiente, "confidence": confianza}


def responder(texto: str = "Respuesta del especialista.", *, confianza: float = 0.9,
              escalar: bool = False, paso: dict | None = None) -> dict:
    datos = {"reply": texto, "confidence": confianza, "needs_more_info": False,
             "suggest_escalation": escalar}
    if paso is not None:
        datos["diagnostic_step"] = paso
    return datos


def calificar(puntaje: float, *problemas: str) -> dict:
    return {"quality_score": puntaje, "hallucination_risk": "low", "clarity": "good",
            "issues": list(problemas)}


def escalamiento(prioridad: str = "high") -> dict:
    return {"reason": "motivo de prueba", "priority": prioridad,
            "summary_for_human": "resumen para el agente", "customer_message": "Te paso con un agente."}
