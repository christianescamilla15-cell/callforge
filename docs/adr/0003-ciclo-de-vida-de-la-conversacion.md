# ADR 0003 · Ciclo de vida explícito de la conversación

- Estado: aceptada · Fecha: 2026-11-20

## Contexto
El estado de la conversación (ACTIVE, RESOLVED, ESCALATED, CLOSED) se cambiaba en tres casos de uso, cada uno
con su propia condición. Faltaba una regla común y había un defecto: `POST /close` con `resolved=true` sobre
una conversación CERRADA la pasaba a RESUELTA y volvía a aceptar mensajes, es decir, reabría un estado
terminal. Tampoco estaba definido qué pasa si el cliente escribe en una conversación RESUELTA: el bot
contestaba y la conversación seguía contándose como resuelta en las métricas.

## Decisión
Una sola tabla de transiciones en `domain/lifecycle.py`, usada por enviar mensaje, cerrar y calificar:

- ACTIVE → ESCALATED (el flujo escala), RESOLVED (calificación o cierre resuelto), CLOSED (cierre).
- RESOLVED → ACTIVE (el cliente vuelve a escribir: se reabre antes de que el bot conteste), CLOSED.
- ESCALATED → RESOLVED o CLOSED (lo cierra el agente humano). Nunca vuelve al bot.
- CLOSED es terminal: no acepta mensajes, no se reabre ni se cierra otra vez (409).

Quedarse en el mismo estado está permitido salvo en CLOSED. Una transición inválida lanza
`InvalidTransitionError` y no modifica la entidad.

## Alternativas
- Mantener las condiciones dispersas: es como apareció el defecto.
- Que un mensaje en una RESUELTA cree una conversación nueva: rompe el hilo y el historial que el
  troubleshooting usa para no repetir pasos.

## Consecuencias
- Las tasas de resolución de `/metrics` cuentan como resueltas sólo las que siguen resueltas.
- Las 16 parejas de estados se prueban (reglas L1–L10); el lote golden recorre conversaciones escaladas.
