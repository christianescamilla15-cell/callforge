# CallForge · API (FastAPI + SQLAlchemy 2 + Alembic). La misma imagen la despliega Render (render.yaml) y la
# construye y ARRANCA el CI (job «imagen»): su Python debe ser el que prueban las pruebas (3.12).
#
# Base anclada por digest: una etiqueta reescrita no cambia la imagen sin que lo veamos en un PR
# (Dependabot propone el digest nuevo y no salta de versión menor de Python).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src \
    DATABASE_URL=sqlite:////app/data/support_agents.db \
    TTS_CACHE_DIR=/app/data/cache/tts \
    VOICE_INBOX_DIR=/app/data/voice_inbox \
    VOICES_DIR=/app/data/voices

WORKDIR /app

# Dependencias en su propia capa (cambian menos que el código), con versión exacta.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# El código es de root y de sólo lectura para el proceso; sólo /app/data se escribe (SQLite, cachés).
COPY src ./src
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin callforge \
    && mkdir -p /app/data \
    && chown callforge:callforge /app/data
USER callforge

EXPOSE 8000

# Prueba de vida sin herramientas extra: /api/v1/health responde 200.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('PORT', '8000'), timeout=3)" || exit 1

# Render inyecta PORT (render.yaml lo fija en 8000); sin él, 8000. Las migraciones corren al arrancar la app.
CMD ["sh", "-c", "exec uvicorn callforge.presentation.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
