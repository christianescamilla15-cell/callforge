# ADR 0002 · Cadena de proveedores de LLM con respaldo determinista

- Estado: aceptada (registrada después de implementarse) · Fecha: 2026-11-20

## Contexto
Cada mensaje cruza hasta seis llamadas a un LLM (router, especialista, calidad, reintento, escalamiento,
resumen). Un proveedor en la nube puede caer, limitar la cuota o no tener llave; uno local puede no estar
corriendo. La plataforma de soporte no puede dejar a un cliente sin respuesta ni devolver un 500 por eso, y
las pruebas no deben depender de la red.

## Decisión
`FallbackLLMProvider` (`infrastructure/llm/fallback.py`) prueba los proveedores en orden y devuelve la
respuesta del primero sano: **Groq → Ollama → Mock** por omisión; con `LLM_PRIMARY=ollama`, **Ollama remoto →
Ollama local → Groq → Mock** (datos en las instalaciones propias, costo cero). El `MockProvider` determinista
es la red final: entra con `MOCK_ENABLED` y también, siempre, si no hay ningún otro proveedor configurado. Si
todos fallan, el flujo responde un mensaje controlado (`phrases.CONTROLLED_FALLBACK`), registra el evento
`fallback_used` y el error de cada proveedor en la corrida, y no crea ticket.

Cada corrida guarda proveedor, modelo, tokens y costo estimado (precio publicado por millón de tokens; los
modelos locales o desconocidos cuestan 0).

## Alternativas
- Un solo proveedor con reintentos: no cubre una caída larga ni la falta de llave.
- Un orquestador de terceros (LangChain/LangGraph): más dependencias para un orden fijo de seis pasos.

## Consecuencias
- El sistema siempre contesta y el CI corre sin red con el mismo código de producción.
- La demo en Render funciona sin llaves (`MOCK_ENABLED=true`), con respuestas deterministas: hay que decirlo
  al presentarla.
- Reglas F1–F8 de `docs/reglas.md`; el lote golden mide el costo en Decimal contra el precio publicado.
