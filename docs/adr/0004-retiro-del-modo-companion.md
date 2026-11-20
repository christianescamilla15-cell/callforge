# ADR 0004 · Retiro del modo «companion» y de los modelos sin censura

- Estado: aceptada · Fecha: 2026-11-20 · Decide: el dueño del repositorio

## Contexto
Sobre la plataforma de soporte se había construido un modo «companion»: una persona de acompañante con
personalidades intercambiables, memoria de largo plazo por embeddings, respuesta en streaming y pruebas de
modelos «uncensored/abliterated» locales. Ese modo reescribió además los prompts de los agentes de soporte
(incluida la instrucción de negar ser una IA). El repositorio se presenta públicamente como plataforma
multiagente de SOPORTE al cliente y ese modo no corresponde a su propósito.

## Decisión
Sale de `main` todo el modo: el campo `persona`, el turno único y el streaming del flujo, la memoria
(entidad, repositorio, modelo, `/api/v1/companion`), las personalidades del webchat, su configuración
(`COMPANION_MODE`, `PERSONA_VOICES`), sus scripts de prueba y el plan `ROADMAP_LLM_LOCAL.md`. Los prompts de
los seis agentes vuelven al tono de soporte, con los mismos contratos JSON. El código retirado sigue en el
historial de Git anterior a este cambio.

La cadena de Alembic no se reescribe: `0005_companion_memory` se queda y `0006` elimina su tabla, con un
`downgrade` que la recrea igual. Los mensajes históricos con `agent_name='companion'` se leen como «sin agente».

## Alternativas
- Dejarlo detrás de una bandera: seguiría publicado y mantenido sin pertenecer al producto.
- Borrar la 0005: rompería las bases que ya la aplicaron.

## Consecuencias
- Al desplegar sobre una base que tenía memorias, la 0006 las borra: respaldar antes si importan (RUNBOOK).
- La configuración de Ollama remoto y `LLM_PRIMARY` se conserva como capacidad genérica (privacidad y el
  maestro del dataset de reembolsos), sin referencias a modelos sin censura.
