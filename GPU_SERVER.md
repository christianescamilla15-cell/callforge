# Servidor GPU on-demand para el cerebro grande

Para correr un modelo uncensored 12-14B (mejor personaje/español/velocidad que
el 4b local) **solo cuando conversas**, pagando por hora. Tu arquitectura ya es
remota-ready: CallForge apunta su Ollama a una IP remota y **cae solo al modelo
local si la caja está apagada** — nunca pierdes el compañero.

## Cómo funciona (la pieza ya está construida)

`.env` tiene dos llaves nuevas:
```
OLLAMA_REMOTE_URL=        # http://<ip>:11434 de la caja GPU (vacío = off)
OLLAMA_REMOTE_MODEL=      # el modelo grande de la caja
```
Cadena resultante (con LLM_PRIMARY=ollama): **remoto → local 4b → groq → mock**.
Si la caja se apaga o no responde, sigue tu 4b local sin que hagas nada.

El toggle: `scripts\gpu_remote.ps1 -Ip <ip> -Model <modelo>` (verifica la caja,
escribe el .env) y `scripts\gpu_remote.ps1 -Off` para volver al local. Luego un
restart elevado de callforge.

---

## Camino A — manual (funciona con CUALQUIER caja: Vast.ai, RunPod, LAN)

El más simple y a prueba de todo:

1. **Renta una GPU** (Vast.ai o TensorDock, RTX 4090 24GB, ~$0.30-0.55/h). Elige
   una plantilla con **Ollama** preinstalado, o una Ubuntu + CUDA y corre:
   `curl -fsSL https://ollama.com/install.sh | sh`
2. En la caja, baja el modelo grande:
   `ollama pull huihui_ai/qwen3-abliterated:14b`   (Apache-2.0, ~9GB)
3. **Expón Ollama de forma segura.** NO lo dejes abierto a internet sin candado.
   Opciones:
   - El **proxy del proveedor** (RunPod da `https://<pod>-11434.proxy.runpod.net`).
   - Un **túnel** (`cloudflared tunnel` o `tailscale`) — tienes cloudflared.
   - O firewall: abre 11434 solo a tu IP.
4. En tu PC: `scripts\gpu_remote.ps1 -Ip <host> -Model huihui_ai/qwen3-abliterated:14b`
   (si usas el proxy https, pasa `-Port 443`).
5. Restart elevado de callforge. Listo: el compañero usa el cerebro grande, con
   tu 4b local de red de seguridad.
6. **Cuando termines**: apaga/borra la caja en el panel del proveedor (deja de
   cobrar) y `scripts\gpu_remote.ps1 -Off` + restart para volver al local.

Costo a 1-2h/día: **~$11-33/mes** (deep-research verificado). Vast.ai no prohíbe
contenido uncensored explícitamente; RunPod tampoco.

---

## Camino B — automatizado (RunPod API, opcional)

`scripts/runpod_gpu.py` arranca/para una caja por script:
1. Cuenta RunPod + crédito + **API key** → `RUNPOD_API_KEY` en `.env`.
2. Crea un **Network Volume** (~20GB) para que el modelo persista entre arranques
   (no re-descargar). Pon su id en `VOLUME_ID` arriba del script.
3. `.venv\Scripts\python.exe -m pip install runpod`
4. `python scripts/runpod_gpu.py start` → arranca Ollama en GPU, imprime la URL.
5. Baja el modelo una vez (curl al `/api/pull` que imprime).
6. `gpu_remote.ps1` con la URL + restart.
7. `python scripts/runpod_gpu.py stop` cuando termines (corta el cobro, guarda el
   volumen).

Es un scaffold honesto: revisa `GPU_TYPE_ID`/`VOLUME_ID` y la cuenta antes de
correr en serio.

---

## Modelo recomendado para la caja (12GB+ VRAM)

- `huihui_ai/qwen3-abliterated:14b` — Apache-2.0, 9GB, generación nueva.
- `huihui_ai/qwen2.5-abliterate:14b` — Apache-2.0, 9GB, limpio (sin la alucinación
  de turnos de los qwen3; aunque el trim ya la maneja).
- Mistral-Nemo-12B uncensored (Apache-2.0) — el mejor español, ~7.5GB.

Todos caben en una 4090/3090 24GB con muchísimo margen y corren a tiempo real.
