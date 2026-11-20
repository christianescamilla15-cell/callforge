#!/usr/bin/env bash
# Prueba de humo posterior a un despliegue (o a una reversa). Sale con 0 sólo si todo responde como debe.
#
#   herramientas/humo.sh <url>
#   HUMO_TOKEN=<API_TOKEN> HUMO_COMMIT=<sha> herramientas/humo.sh https://<servicio>.onrender.com
#
# · /api/v1/health: status=ok, base de datos ok; imprime versión, commit y cadena de LLM.
# · HUMO_COMMIT (opcional): el commit que reporta /health debe ser ése (se verifica el artefacto).
# · /api/v1/metrics sin credencial → 401: contesta la app real, con la API protegida (en Render,
#   API_TOKEN lo genera el Blueprint). HUMO_SIN_TOKEN=1 si el servidor corre en modo abierto.
# · HUMO_TOKEN (opcional): abre una conversación, manda un mensaje y exige una respuesta del flujo.
# · /webchat sirve la página.
# El plan gratuito de Render duerme: cada llamada espera hasta HUMO_TIMEOUT segundos (90 por omisión).
set -uo pipefail

API="${1:?uso: herramientas/humo.sh <url>}"
API="${API%/}"
TIMEOUT="${HUMO_TIMEOUT:-90}"
fallas=0

ok()    { echo "  ok    $1"; }
falla() { echo "  FALLA $1"; fallas=$((fallas + 1)); }
campo() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$1') if isinstance(d, dict) else '')" 2>/dev/null; }

echo "humo: ${API}"

salud="$(curl -fsS --max-time "$TIMEOUT" "${API}/api/v1/health" 2>&1)"
if [ "$(printf '%s' "$salud" | campo status)" = "ok" ] && [ "$(printf '%s' "$salud" | campo database)" = "ok" ]; then
  ok "/api/v1/health status=ok database=ok version=$(printf '%s' "$salud" | campo version) commit=$(printf '%s' "$salud" | campo commit) llm=$(printf '%s' "$salud" | campo llm_providers)"
else
  falla "/api/v1/health: ${salud:0:200}"
fi

if [ -n "${HUMO_COMMIT:-}" ]; then
  desplegado="$(printf '%s' "$salud" | campo commit)"
  if [ "$desplegado" = "$HUMO_COMMIT" ]; then
    ok "el artefacto desplegado es el commit ${HUMO_COMMIT}"
  else
    falla "commit desplegado «${desplegado}», se esperaba «${HUMO_COMMIT}»"
  fi
fi

codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "${API}/api/v1/metrics")"
if [ "${HUMO_SIN_TOKEN:-0}" = "1" ]; then
  [ "$codigo" = "200" ] && ok "/api/v1/metrics en modo abierto → 200" || falla "/api/v1/metrics devolvió ${codigo}"
elif [ "$codigo" = "401" ]; then
  ok "/api/v1/metrics sin credencial → 401"
else
  falla "/api/v1/metrics sin credencial devolvió ${codigo} (se esperaba 401)"
fi

if [ -n "${HUMO_TOKEN:-}" ]; then
  cabecera="X-API-Token: ${HUMO_TOKEN}"
  cid="$(curl -fsS --max-time "$TIMEOUT" -H "$cabecera" -H 'Content-Type: application/json' \
         -d '{"customer_external_id":"humo"}' "${API}/api/v1/conversations/start" | campo conversation_id)"
  if [ -n "$cid" ] && [ "$cid" != "None" ]; then
    turno="$(curl -fsS --max-time "$TIMEOUT" -H "$cabecera" -H 'Content-Type: application/json' \
             -d '{"content":"Hola, una pregunta sobre el servicio"}' \
             "${API}/api/v1/conversations/${cid}/message")"
    agente="$(printf '%s' "$turno" | campo agent_used)"
    respuesta="$(printf '%s' "$turno" | campo reply)"
    if [ -n "$agente" ] && [ "$agente" != "None" ] && [ -n "$respuesta" ]; then
      ok "un turno de conversación responde (agente=${agente})"
    else
      falla "el turno de conversación no respondió: ${turno:0:200}"
    fi
  else
    falla "no se pudo abrir una conversación con HUMO_TOKEN"
  fi
fi

if curl -fsS --max-time "$TIMEOUT" "${API}/webchat" | grep -q 'CallForge'; then
  ok "/webchat sirve la página"
else
  falla "/webchat no sirve la página"
fi

if [ "$fallas" -gt 0 ]; then
  echo "humo: ${fallas} falla(s)"
  exit 1
fi
echo "humo: todo en orden"
