# Arquitectura · Switchboard (CallForge)

Modelo C4: contexto → contenedores → componentes. Los diagramas son texto (Mermaid) y se versionan con el
código; cada caja corresponde a un módulo real del repositorio. Decisiones en [`docs/adr/`](../adr/), reglas y
sus pruebas en [`docs/reglas.md`](../reglas.md), contrato de la API en [`docs/api/openapi.json`](../api/openapi.json).

## 1. Contexto

```mermaid
C4Context
  title Contexto · CallForge (plataforma multiagente de soporte al cliente)
  Person(cliente, "Cliente final", "Escribe o habla con soporte por webchat, REST o voz")
  Person(operador, "Operador del tenant", "Carga conocimiento, atiende tickets escalados, ve métricas")
  Person(admin, "Administrador", "Da de alta empresas (tenants) y emite sus API keys")
  System(callforge, "CallForge", "Enruta cada mensaje entre 6 agentes, escala a humanos con ticket y deja traza de cada decisión")
  System_Ext(groq, "Groq", "LLM en la nube y Whisper (voz a texto)")
  System_Ext(ollama, "Ollama", "LLM y embeddings locales o en una caja GPU bajo demanda")
  System_Ext(cartesia, "Cartesia", "Texto a voz en la nube (opcional)")
  Rel(cliente, callforge, "Conversa", "HTTPS / WebSocket")
  Rel(operador, callforge, "Opera", "HTTPS + X-API-Token")
  Rel(admin, callforge, "Administra", "HTTPS + X-Admin-Token")
  Rel(callforge, groq, "Completa y transcribe", "HTTPS")
  Rel(callforge, ollama, "Completa y embebe", "HTTP")
  Rel(callforge, cartesia, "Sintetiza voz", "HTTPS")
```

Sin ninguna llave ni Ollama, CallForge contesta con el `MockProvider` determinista: así corre el CI y así
arranca la demo en Render (`MOCK_ENABLED=true`).

## 2. Contenedores

```mermaid
C4Container
  title Contenedores · CallForge
  Person(cliente, "Cliente final")
  Person(operador, "Operador del tenant")
  System_Boundary(cf, "CallForge") {
    Container(web, "Webchat y panel", "HTML + JS servidos por la API", "Chat con micrófono y voz (/webchat) y panel de métricas y tickets (/dashboard)")
    Container(api, "API", "Python 3.12 · FastAPI · uvicorn (imagen Docker)", "REST /api/v1, WebSocket /webchat/ws, flujo de agentes, migraciones al arrancar")
    ContainerDb(bd, "Base de datos", "SQLite (Render, local) o PostgreSQL 16 (compose)", "Tenants, conversaciones, mensajes, tickets, pasos, corridas, eventos, uso de LLM")
    Container(voz, "Servidor de voz", "Python · FastAPI · torch (venv aparte, :8002)", "Clonación de voz (Chatterbox/OpenVoice); opcional")
  }
  System_Ext(groq, "Groq")
  System_Ext(ollama, "Ollama")
  Rel(cliente, web, "Usa", "HTTPS")
  Rel(web, api, "Mensajes y audio", "WebSocket / JSON")
  Rel(operador, api, "Tickets, conocimiento, métricas", "JSON/HTTPS")
  Rel(api, bd, "Lee y escribe", "SQLAlchemy 2 + Alembic")
  Rel(api, voz, "Convierte el timbre de la voz", "HTTP")
  Rel(api, groq, "Chat completions y Whisper", "HTTPS")
  Rel(api, ollama, "Chat y embeddings", "HTTP")
```

## 3. Componentes de la API

```mermaid
C4Component
  title Componentes · API (src/callforge)
  Container_Boundary(api, "API") {
    Component(rutas, "Rutas", "presentation/api/routes", "conversations, tickets, knowledge, feedback, admin, system, voice, webchat")
    Component(deps, "Autenticación y tenancy", "presentation/api/deps.py", "Resuelve X-API-Token → tenant; admin aparte")
    Component(casos, "Casos de uso", "application/use_cases", "Start/SendMessage, Close, Feedback, Knowledge, Tickets; UnitOfWork por petición")
    Component(ciclo, "Ciclo de vida", "domain/lifecycle.py", "Transiciones permitidas de la conversación")
    Component(flujo, "SupportWorkflow", "orchestration/workflow.py", "Router → conocimiento → especialista → calidad → reintento → escalamiento")
    Component(politica, "EscalationPolicy", "orchestration/policies.py", "Umbrales de calidad y confianza, reintentos")
    Component(agentes, "Agentes", "agents/", "Router, Support, Troubleshooting, Quality, Escalation, Summarizer")
    Component(cadena, "Cadena de LLM", "infrastructure/llm", "Groq → Ollama → Mock con respaldo y costo estimado")
    Component(conocimiento, "Conocimiento híbrido", "infrastructure/knowledge", "Embeddings con caída a palabras clave")
    Component(repos, "Repositorios y métricas", "infrastructure/repositories.py", "SQLAlchemy por tenant; MetricsReader")
    Component(vozc, "Voz", "infrastructure/voice", "Whisper STT; Kokoro, Chatterbox, Cartesia con respaldo y caché")
  }
  ContainerDb(bd, "Base de datos")
  System_Ext(groq, "Groq")
  Rel(rutas, deps, "Autentica")
  Rel(rutas, casos, "Invoca")
  Rel(casos, ciclo, "Mueve el estado")
  Rel(casos, flujo, "Corre un turno")
  Rel(flujo, politica, "Decide reintento y escalamiento")
  Rel(flujo, agentes, "Llama en orden")
  Rel(agentes, cadena, "Completa JSON")
  Rel(flujo, conocimiento, "Busca contexto")
  Rel(casos, repos, "Persiste")
  Rel(repos, bd, "SQL")
  Rel(rutas, vozc, "Transcribe y sintetiza")
  Rel(cadena, groq, "HTTPS")
```

## 4. Un turno de soporte

```mermaid
flowchart TD
    M[Mensaje del cliente] --> E{Estado}
    E -->|CLOSED| X[409]
    E -->|ESCALATED| H[Se guarda para el humano<br/>aviso fijo, sin bot]
    E -->|RESOLVED| R[Se reabre: ACTIVE]
    E -->|ACTIVE| RT
    R --> RT[RouterAgent<br/>intención, urgencia, siguiente]
    RT -->|escalation| ES[EscalationAgent + SummarizerAgent<br/>ticket y conversación ESCALATED]
    RT -->|support / troubleshooting| K[Conocimiento del tenant]
    K --> SP[Especialista<br/>troubleshooting propone un paso]
    SP --> Q[QualityAgent]
    Q -->|baja y quedan reintentos| SP
    Q --> P{EscalationPolicy}
    P -->|sugiere escalar · confianza baja · calidad baja agotada| ES
    P -->|ok| OK[Respuesta]
    RT -. LLM caído .-> F[Respuesta controlada<br/>evento fallback_used]
    SP -. LLM caído .-> F
```

Cada agente deja una corrida (`agent_runs`: entrada, salida, decisión, confianza, latencia, modelo, proveedor,
tokens, costo, error) y el flujo sus eventos (`agent_events`): esa traza alimenta `/metrics` y el lote golden.

## 5. Ciclo de vida de la conversación

```mermaid
stateDiagram-v2
    [*] --> ACTIVE
    ACTIVE --> ESCALATED: el flujo escala
    ACTIVE --> RESOLVED: calificación «resuelta» / cierre resuelto
    ACTIVE --> CLOSED: cierre no resuelto
    RESOLVED --> ACTIVE: el cliente vuelve a escribir
    RESOLVED --> CLOSED: se archiva
    ESCALATED --> RESOLVED: el humano la cierra resuelta
    ESCALATED --> CLOSED: el humano la cierra
    CLOSED --> [*]
```

## 6. Despliegue

| Pieza | Dónde | Cómo |
|---|---|---|
| API (imagen `Dockerfile`) | Render, servicio web `callforge` (plan free) | Blueprint `render.yaml`, `autoDeploy` desde `main`, `healthCheckPath: /api/v1/health` |
| Base de datos | SQLite dentro del contenedor (`/app/data`) | Sin disco persistente en el plan free: se pierde en cada despliegue (ver RUNBOOK) |
| Servidor de voz | Equipo con GPU, fuera de Render | Manual (`voice_server.py`, venv con torch) |
| Servicio local | Windows con nssm | `scripts/install-windows-service.ps1` (manual) |

Detalle, variables, verificación y reversa en [`RUNBOOK.md`](../../RUNBOOK.md).
