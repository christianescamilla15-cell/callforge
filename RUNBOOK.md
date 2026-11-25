# Runbook · Switchboard (CallForge)

Despliegue real a la fecha (2026-11-20), según lo que declara el repositorio. **No se verificó desde esta
máquina** ningún servicio publicado en `*.onrender.com`: el nombre exacto del servicio, si el Blueprint ya
está conectado y qué commit corre hoy se confirman en el panel de Render.

| Pieza | Plataforma | Recurso | Disparador |
|---|---|---|---|
| API (imagen `Dockerfile`, Python 3.12, usuario 10001) | Render, servicio web Docker, plan free | `callforge` en `render.yaml`; `healthCheckPath: /api/v1/health` | `autoDeploy: true`: cada push a `main` |
| Base de datos | SQLite en el contenedor (`/app/data/support_agents.db`) | sin disco persistente en el plan free | se recrea vacía en cada despliegue o reinicio; las migraciones corren al arrancar |
| Servicio local (opcional) | Windows con nssm | `scripts/install-windows-service.ps1`, `install-local-deploy.ps1` (+ Ollama), `install-voice-service.ps1` (:8002) | manual |
| Servidor de voz y GPU (opcional) | equipo propio con GPU o caja bajo demanda | `voice_server.py`, `GPU_SERVER.md` | manual |

Variables en Render (`render.yaml`): `MOCK_ENABLED=true` (la demo contesta sin llaves), `PORT=8000`,
`API_TOKEN` generado por el Blueprint (protege toda la API salvo `/api/v1/health`) y `GROQ_API_KEY` a mano en
el panel (`sync: false`; sin ella responde Ollama si existe, si no el Mock). Opcionales: `ADMIN_TOKEN` (activa
el alta de tenants), `LLM_PRIMARY`, `OLLAMA_*`, `TTS_*`, `CARTESIA_*`. Lista completa en `.env.example` y
`src/callforge/config.py`. Render define `RENDER_GIT_COMMIT` en el servicio (según su documentación de variables
por omisión) y `/api/v1/health` la devuelve como `commit`.

## Desplegar

1. Todo cambio entra por PR a `main` con los checks en verde: `ci` (pruebas en SQLite y PostgreSQL, imagen
   construida, escaneada y ARRANCADA con la configuración de `render.yaml`, humo y E2E contra el contenedor)
   y `seguridad` (confidencialidad, gitleaks, CodeQL, Semgrep, Trivy, SBOM). Los checks requeridos están
   declarados en `.github/rulesets/main.json`.
2. Al fusionar, Render construye la imagen del `Dockerfile` y la publica cuando `/api/v1/health` responde.
3. Prueba de humo contra producción (debe salir con 0); el token es el `API_TOKEN` del panel de Render:

   ```bash
   HUMO_TOKEN=<API_TOKEN> HUMO_COMMIT=$(git rev-parse origin/main) \
     herramientas/humo.sh https://<servicio>.onrender.com
   ```

   Revisa `/api/v1/health` (estado, base, versión, cadena de LLM y que el commit desplegado sea el de `main`),
   que `/api/v1/metrics` sin credencial dé 401 (contesta la app real, protegida), un turno de conversación
   completo y que `/webchat` sirva la página. El plan free duerme: la primera llamada puede tardar ~1 min.
4. El E2E por rol también corre contra un servidor vivo si se le dan el `ADMIN_TOKEN` y el `API_TOKEN`:
   `CALLFORGE_E2E_URL=... CALLFORGE_E2E_ADMIN_TOKEN=... CALLFORGE_E2E_API_TOKEN=... pytest tests/e2e`.

**Pendiente (N09):** Render despliega con cada push a `main` sin esperar al CI y sin entorno de preview. Para
tener la compuerta, cambiar en el panel el disparador del servicio a «después de que pasen los checks» (o
`autoDeploy: false` + deploy hook desde el CI) y activar el ruleset; no se cambió en esta rama porque altera
producción y necesita el OK del dueño.

## Revertir (no ensayado todavía)

1. Render → servicio `callforge` → *Events/Deploys* → último despliegue verde → **Rollback**.
2. `herramientas/humo.sh` sobre la versión restaurada, con `HUMO_COMMIT` = el commit al que se volvió.
3. Base de datos: en Render es efímera (no hay nada que revertir). En una instalación con base persistente
   (Windows/nssm o compose), el rollback de código NO revierte migraciones: respaldar el archivo SQLite o
   `pg_dump` antes de actualizar y, si hace falta, `alembic downgrade <revisión>` (todas traen `downgrade`).

El ensayo de la reversa (fecha y evidencia) queda pendiente: requiere acceso al panel de Render.

## Servicio local en Windows (nssm)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -e .
.\scripts\install-windows-service.ps1            # API en :8000 como servicio «callforge»
nssm restart callforge                           # tras cambiar variables (no basta parar/iniciar desde el SCM)
python scripts\smoke_service.py                  # humo del servicio instalado
```

**Actualizar a esta versión una base que ya corrió el modo companion:** la migración `0006` elimina la tabla
`companion_memories` (ver ADR 0004). Respaldar `support_agents.db` antes de reiniciar si esas memorias
importan; el resto de los datos de soporte no se toca.

## Scripts manuales (no entran al CI)

Necesitan red, una llave, Ollama, una GPU o RunPod, y miden calidad o velocidad de modelos reales:

| Script | Necesita | Para qué |
|---|---|---|
| `scripts/ci.ps1`, `pipeline_smoke.py`, `e2e_probe.py`, `smoke_service.py`, `smoke_post_restart.py`, `smoke_cascade.py` | instancia local (Windows) con LLM real | pipeline local y humo con el modelo de verdad |
| `scripts/model_bakeoff.py`, `probe_embeddings.py`, `ollama_speed.ps1` | Ollama local | comparar modelos, calibrar el umbral de similitud, acelerar Ollama |
| `scripts/refund_eval.py`, `gen_refund_dataset.py`, `finetune_refund.py` | Ollama/LLM maestro; GPU para el fine-tune | especialista de reembolsos ([FINETUNE_RUNBOOK.md](FINETUNE_RUNBOOK.md)) |
| `scripts/evaluar_rag_local.py`, `python -m callforge.evaluation.rag embeddings\|llm` | `llama-cpp-python` y GGUF, u Ollama | regenerar los vectores del caché de evaluación o la instantánea de respuestas de un LLM local con su juez ([docs/evaluacion_rag.md](docs/evaluacion_rag.md)) |
| `scripts/runpod_gpu.py`, `gpu_remote.ps1` | cuenta de RunPod o caja GPU | Ollama remoto bajo demanda ([GPU_SERVER.md](GPU_SERVER.md)) |
| `scripts/smoke_voice.py`, `smoke_voice_prod.py`, `build_voice_bank.py`, `voice_ab.py`, `ab_source_voice.py`, `test_chatterbox.py`, `test_openvoice_vc.py`, `test_qwen3_tts.py`, `extract_voice.py`, `diarize.py`, `cartesia_*.py` | GPU, `.venv-voice` con torch, Groq o Cartesia | voz: clonación, banco de frases, pruebas A/B ([ROADMAP_VOZ.md](ROADMAP_VOZ.md)) |

## Diagnóstico

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `/api/v1/health` con `llm_providers: ["ollama","mock"]` y respuestas genéricas | Sin `GROQ_API_KEY` en Render; Ollama no existe ahí y cae al Mock | Cargar `GROQ_API_KEY` en el panel; las respuestas del Mock son deterministas a propósito |
| Las conversaciones desaparecen | SQLite efímero en el plan free (cada despliegue o reinicio empieza vacío) | Esperado en la demo; para conservar datos, un disco persistente o PostgreSQL |
| 401 en todo salvo `/health` | `API_TOKEN` está definido (el Blueprint lo genera) | Mandar `X-API-Token`; en el webchat, `?token=` en la URL |
| `POST /admin/tenants` → 403 | `ADMIN_TOKEN` vacío: el alta de tenants está apagada | Definir `ADMIN_TOKEN` en el panel |
| `POST /voice/tts` → 500 en Render | La imagen no instala la voz local (extra `[voice]`: kokoro-onnx) y `TTS_ENABLED` vale `true` por omisión | `TTS_ENABLED=false` en Render, o `TTS_ENGINE=cartesia` con `CARTESIA_API_KEY` |
| Mensajes con «Perdona, tuve un problema para responderte» | Todos los proveedores de LLM fallaron (evento `fallback_used`) | Revisar el error de la corrida en `agent_runs.error` y las llaves/Ollama |
| El contenedor no arranca tras actualizar con base propia | Migración contra una base creada fuera de Alembic | `alembic stamp head` sólo si el esquema ya coincide; si no, respaldar y migrar |
| No arranca con `WORKFLOW_ENGINE=langgraph` («necesita el adaptador») | La imagen de producción no instala LangGraph (ADR 0005) | `pip install -r requirements-langgraph.txt`, o volver a `state_machine` (el motor por omisión) |
| `docker compose --profile postgres up` no levanta Postgres | Falta `POSTGRES_PASSWORD` en `.env` (no hay contraseña por omisión) | Definirla en `.env` |

Observabilidad: bitácora en vivo en Render; cada decisión de agente queda en `agent_runs`/`agent_events` y se
resume en `/api/v1/metrics` y `/dashboard`. Con el motor LangGraph, cada nodo deja además una línea
`langgraph nodo=… ms=… agentes=… siguiente=…` en la bitácora.
