# CallForge

Plataforma de automatización de atención a clientes (call center) basada en
agentes LLM, con Clean Architecture, trazabilidad completa y costo cero por
defecto. API-first: hoy REST, mañana WhatsApp/webchat/email/CRM/telefonía sin
tocar el dominio.

## ADN (lo mejor de proyectos previos, integrado — no Frankenstein)

| Patrón | Origen | Dónde vive aquí |
|---|---|---|
| Agentes + workflows + eventos + trazabilidad | NexusForge | `agents/`, `orchestration/`, tablas `agent_runs`/`agent_events` |
| Cadena de fallback con degradación limpia | YT backend (`playbackFallback`) | `infrastructure/llm/fallback.py` (Groq → Ollama → Mock) |
| Degradar a humano sin romper la experiencia | Verificarro (manual-first) | `EscalationAgent` + `EscalationPolicy` |
| Groq como LLM remoto barato/free | Compras semanales, YT `/recommend` | `GroqProvider` |
| Token compartido opcional para exponer el API | YT token split | `API_TOKEN` + header `X-API-Token` |
| Verify-before-ship, tests por fase | Spacetime Lab | `tests/` (corre 100% offline con MockProvider) |
| Despliegue local como servicio Windows | yt-extract-service | `scripts/install-windows-service.ps1` (nssm) |

## Arquitectura

```
src/callforge/
  domain/            # Entidades puras + value objects + puertos (sin frameworks)
  application/       # Casos de uso + DTOs + UnitOfWork
  agents/            # Router, Support, Troubleshooting, Escalation, Summarizer, Quality
  orchestration/     # SupportWorkflow + EscalationPolicy (orquestación propia, sin LangGraph)
  infrastructure/    # SQLAlchemy, providers LLM, knowledge store, métricas
  presentation/api/  # FastAPI: rutas, schemas, deps
tests/               # 100% offline (MockProvider + SQLite temporal)
```

Regla de dependencias: `presentation → application → domain` e
`infrastructure` implementa los puertos del dominio. Los endpoints no
contienen lógica de negocio.

### Flujo de un mensaje

1. `RouterAgent` clasifica intención, categoría, urgencia, frustración y decide el siguiente agente.
2. Se recupera contexto de la base de conocimiento (keyword retrieval local, cero dependencias; swap futuro a ChromaDB/FAISS sin tocar agentes).
3. `SupportAgent` o `TroubleshootingAgent` responde usando SOLO ese contexto.
4. `QualityAgent` evalúa la respuesta (score, riesgo de alucinación). Si es baja: 1 reintento con feedback.
5. `EscalationPolicy` decide escalar (pedido de humano, sugerencia del agente, confianza baja, calidad baja tras reintentos) → `EscalationAgent` genera el handoff + `SummarizerAgent` resume → se crea Ticket + Escalation.
6. Todo queda registrado: mensajes, `agent_runs` (input/output/decision/confidence/latency/modelo/provider/tokens/costo/error), eventos de workflow y uso LLM.

### Fallback LLM

`Groq (si hay API key) → Ollama (si está habilitado) → Mock determinista`.
Si TODO falla, el workflow responde con un mensaje controlado y lo registra
como evento `fallback_used`. El sistema nunca se cae por el LLM.

## Correr local (sin pagar nada)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
python main.py        # http://localhost:8000/docs
```

Sin configurar nada responde con MockProvider. Para respuestas reales:
- Pon `GROQ_API_KEY` en `.env` (free tier), o
- Levanta Ollama local (`ollama run llama3.1`).

### Docker

```bash
docker compose up --build
# Postgres opcional: docker compose --profile postgres up
```

## Ejemplos (curl)

```bash
# Salud
curl http://localhost:8000/api/v1/health

# Cargar conocimiento
curl -X POST http://localhost:8000/api/v1/knowledge/documents \
  -H "Content-Type: application/json" \
  -d '{"title":"Como reiniciar el modem","content":"Desconecta el modem 60 segundos y vuelve a conectarlo.","tags":["internet"]}'

# Iniciar conversación
curl -X POST http://localhost:8000/api/v1/conversations/start \
  -H "Content-Type: application/json" \
  -d '{"customer_external_id":"cliente-42","customer_name":"Ana"}'

# Enviar mensaje (usa el conversation_id devuelto)
curl -X POST http://localhost:8000/api/v1/conversations/<ID>/message \
  -H "Content-Type: application/json" \
  -d '{"content":"Mi internet no funciona desde ayer"}'

# Ver conversación / tickets / métricas
curl http://localhost:8000/api/v1/conversations/<ID>
curl http://localhost:8000/api/v1/tickets
curl http://localhost:8000/api/v1/metrics

# Feedback
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"<ID>","rating":5,"resolved":true}'
```

Si defines `API_TOKEN` en `.env`, agrega `-H "X-API-Token: <token>"` a cada
request (`/health` queda abierto para probes).

## Voz

El asistente habla y escucha (botones 🎤/🔊 en `/webchat`):

- **Entrada (STT)**: Groq `whisper-large-v3-turbo` ($0.04/hora, ~0.9s por turno, español verificado palabra por palabra).
- **Salida (TTS) en dos niveles**:
  - **Kokoro** (`kokoro-onnx`, default): corre 100% en CPU — la VRAM queda para el LLM. RTF ~0.34 medido. Modelos en `models/` (descarga: releases de `thewh1teagle/kokoro-onnx`, archivos `kokoro-v1.0.onnx` + `voices-v1.0.bin`).
  - **Chatterbox Multilingual** (clonación de voz, MIT): micro-servicio aparte (`voice_server.py`, venv propio `.venv-voice` con torch cu124) en `:8002`. Clona desde ~10s de audio: pon un WAV en `voices/` y configura `CHATTERBOX_VOICE=<nombre>`. Con `TTS_ENGINE=chatterbox`, CallForge lo intenta primero y **cae a Kokoro automáticamente** si está caído.

```powershell
# venv de voz (una vez):
python -m venv .venv-voice
.\.venv-voice\Scripts\python.exe -m pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
.\.venv-voice\Scripts\python.exe -m pip install chatterbox-tts
# OJO: chatterbox arrastra torch CPU de PyPI; reinstala los wheels cu124 DESPUÉS.

# correr el voice server:
.\.venv-voice\Scripts\python.exe -m uvicorn voice_server:app --port 8002
# o como servicio Windows: scripts\install-voice-service.ps1 (elevado)
```

Endpoints: `POST /api/v1/voice/tts` (texto → WAV) y `POST /api/v1/conversations/{id}/voice-message` (audio → STT → workflow → respuesta + audio hablado).

## Tests

```powershell
pytest
```

Todos los tests corren offline (MockProvider + SQLite en tmp). Cubren:
router, fallback de providers, flujo de conversación end-to-end, política y
flujo de escalación, retrieval de conocimiento, health/metrics/feedback y el
gate de token.

## Roadmap técnico

- ✅ **Migraciones**: Alembic dentro del paquete, stamp automático para DBs pre-Alembic, upgrade en startup.
- ✅ **Retrieval**: embeddings locales (`nomic-embed-text` vía Ollama) detrás de `search()`, backfill perezoso + fallback keyword. Floor de similitud 0.55 calibrado empíricamente.
- ✅ **Resolución guiada**: `ResolutionStep` persistente — el TroubleshootingAgent ve los pasos previos y no los repite; expuestos en `GET /conversations/{id}`.
- ✅ **Webchat**: página en `/webchat` + WebSocket `/webchat/ws` reutilizando los mismos casos de uso.
- ✅ **Dashboard**: `/dashboard` sobre `/metrics` + bandeja de tickets, auto-refresh.
- ✅ **Multi-tenant**: tabla `tenants` + `tenant_id` en agregados raíz, scoping dentro de los repos, API keys por tenant (`POST /api/v1/admin/tenants` con `X-Admin-Token`), métricas por tenant. El tenant `default` mantiene el modo single-tenant sin fricción.
- **WhatsApp / email**: adaptadores de canal pendientes (requieren credenciales Meta/SMTP); el patrón es el de `routes/webchat.py` — solo presentación.
- **Redis**: cache de sesiones/respuestas si el volumen lo pide (decisión: diferido, no hay volumen que lo justifique).
- **Embeddings vectoriales dedicados**: migrar de cosine-en-Python a ChromaDB/FAISS cuando la base supere ~miles de documentos.
