"""Deterministic provider: final safety net of the fallback chain and the
test backbone. Recognizes which agent is calling by a marker in the system
prompt and answers with realistic structured JSON, applying simple keyword
heuristics over the customer message (Spanish + English)."""
from __future__ import annotations

import json

from callforge.infrastructure.llm.base import LLMProvider, LLMResult

_HUMAN_WORDS = (
    "humano", "human", "supervisor", "gerente", "manager", "agente real",
    "persona real", "hablar con alguien",
)
_BILLING_WORDS = (
    "factura", "facturacion", "cobro", "cargo", "billing", "charge",
    "refund", "reembolso", "pago", "payment", "invoice",
)
_TECH_WORDS = (
    "error", "no funciona", "not working", "falla", "fails", "crash",
    "internet", "modem", "router", "lento", "slow", "no carga", "broken",
    "se cae", "intermitente",
)


def _contains(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in words)


class MockProvider(LLMProvider):
    name = "mock"

    async def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        json_mode: bool = False,
    ) -> LLMResult:
        user_text = messages[-1]["content"] if messages else ""
        text = self._respond(system, user_text)
        return LLMResult(
            text=text,
            provider=self.name,
            model="mock",
            tokens_in=len(user_text.split()),
            tokens_out=len(text.split()),
            latency_ms=1,
            estimated_cost=0.0,
        )

    def _respond(self, system: str, user_text: str) -> str:
        if "memoria de un acompañante" in system:
            return "NADA"  # deterministic: extract nothing in tests
        if "CompanionAgent" in system:
            # Companion mode is plain text, not JSON.
            return (
                "Aquí estoy contigo. Cuéntame, ¿qué traes en la cabeza hoy? "
                "Te escucho sin prisa."
            )
        if "RouterAgent" in system:
            return self._route(user_text)
        if "TroubleshootingAgent" in system:
            already_tried = "PREVIOUS DIAGNOSTIC STEPS" in user_text
            if already_tried:
                return json.dumps(
                    {
                        "reply": (
                            "Gracias por probar el paso anterior. Paso 2: verifica "
                            "que el cable de red esté firmemente conectado. "
                            "¿Cambió algo?"
                        ),
                        "confidence": 0.8,
                        "needs_more_info": True,
                        "suggest_escalation": False,
                        "diagnostic_step": {
                            "instruction": "Verificar conexion del cable de red",
                            "expected_check": "La luz de internet queda fija",
                        },
                    }
                )
            return json.dumps(
                {
                    "reply": (
                        "Vamos a diagnosticarlo paso a paso. Paso 1: reinicia el "
                        "equipo y espera 60 segundos. ¿El problema persiste "
                        "después de reiniciar?"
                    ),
                    "confidence": 0.8,
                    "needs_more_info": True,
                    "suggest_escalation": False,
                    "diagnostic_step": {
                        "instruction": "Reiniciar el equipo y esperar 60 segundos",
                        "expected_check": "El problema desaparece tras reiniciar",
                    },
                }
            )
        if "EscalationAgent" in system:
            return json.dumps(
                {
                    "reason": "El cliente solicitó atención humana o el flujo automático no pudo resolver.",
                    "priority": "high",
                    "summary_for_human": f"Caso escalado. Último mensaje del cliente: {user_text[:300]}",
                    "customer_message": (
                        "Entiendo, voy a transferir tu caso con un agente humano. "
                        "Ya registré el contexto de tu problema para que no tengas "
                        "que repetirlo. Te contactarán a la brevedad."
                    ),
                }
            )
        if "SummarizerAgent" in system:
            return json.dumps(
                {
                    "problem": user_text[:200] or "Consulta del cliente",
                    "actions_taken": ["Clasificación automática", "Respuesta inicial del agente"],
                    "final_status": "in_progress",
                    "next_steps": ["Seguimiento por agente humano si fue escalado"],
                }
            )
        if "QualityAgent" in system:
            return json.dumps(
                {
                    "quality_score": 0.85,
                    "hallucination_risk": "low",
                    "clarity": "good",
                    "issues": [],
                }
            )
        # SupportAgent / default
        return json.dumps(
            {
                "reply": (
                    "Gracias por tu consulta. Con la información disponible: "
                    "puedo ayudarte con preguntas sobre el servicio, facturación "
                    "y problemas técnicos. ¿Podrías darme un poco más de detalle "
                    "para darte una respuesta precisa?"
                ),
                "confidence": 0.85,
                "needs_more_info": False,
                "suggest_escalation": False,
            }
        )

    def _route(self, user_text: str) -> str:
        if _contains(user_text, _HUMAN_WORDS):
            payload = {
                "intent": "human_request",
                "category": "escalation",
                "urgency": "high",
                "frustration": "high",
                "next_agent": "escalation",
                "confidence": 0.95,
            }
        elif _contains(user_text, _TECH_WORDS):
            payload = {
                "intent": "technical_issue",
                "category": "technical",
                "urgency": "medium",
                "frustration": "medium",
                "next_agent": "troubleshooting",
                "confidence": 0.9,
            }
        elif _contains(user_text, _BILLING_WORDS):
            payload = {
                "intent": "billing",
                "category": "billing",
                "urgency": "medium",
                "frustration": "low",
                "next_agent": "support",
                "confidence": 0.9,
            }
        else:
            payload = {
                "intent": "question",
                "category": "general",
                "urgency": "low",
                "frustration": "low",
                "next_agent": "support",
                "confidence": 0.8,
            }
        return json.dumps(payload)
