# Servidor GPU bajo demanda (Ollama remoto)

Para correr un modelo más grande que el local **sólo mientras se necesita**, pagando por hora: el
«maestro» que genera el dataset del agente de reembolsos ([FINETUNE_RUNBOOK.md](FINETUNE_RUNBOOK.md)),
o un modelo mayor para comparar contra el especialista afinado con `scripts/refund_eval.py`.
CallForge apunta su Ollama a una IP remota y **cae solo al modelo local si la caja está apagada**.

> Manual: nada de esto corre en el CI (necesita GPU y una cuenta del proveedor). Ver RUNBOOK.md.

## Cómo funciona (la pieza ya está construida)

`.env` tiene dos llaves:
```
OLLAMA_REMOTE_URL=        # http://<ip>:11434 de la caja GPU (vacío = apagado)
OLLAMA_REMOTE_MODEL=      # el modelo grande de la caja
```
Cadena resultante (con `LLM_PRIMARY=ollama`): **remoto → local → groq → mock**.
Si la caja se apaga o no responde, sigue el modelo local sin que hagas nada.

El interruptor: `scripts\gpu_remote.ps1 -Ip <ip> -Model <modelo>` (verifica la caja y escribe el `.env`)
y `scripts\gpu_remote.ps1 -Off` para volver al local. Después, reinicia el servicio de CallForge.

---

## Camino A — manual (cualquier proveedor: Vast.ai, RunPod, una caja en la LAN)

1. **Renta una GPU** (p. ej. RTX 4090 de 24 GB). Elige una plantilla con **Ollama** preinstalado, o una
   Ubuntu + CUDA y corre: `curl -fsSL https://ollama.com/install.sh | sh`
2. En la caja, baja el modelo: `ollama pull mistral-small:24b` (o el que vayas a usar de maestro).
3. **Expón Ollama de forma segura.** NO lo dejes abierto a internet sin candado:
   - el **proxy del proveedor** (RunPod da `https://<pod>-11434.proxy.runpod.net`);
   - un **túnel** (`cloudflared tunnel` o `tailscale`);
   - o un firewall que abra 11434 sólo a tu IP.
4. En tu PC: `scripts\gpu_remote.ps1 -Ip <host> -Model mistral-small:24b` (con el proxy https, `-Port 443`).
5. Reinicia CallForge: el modelo grande responde primero y el local queda de red de seguridad.
6. **Cuando termines**: apaga o borra la caja en el panel del proveedor (deja de cobrar) y
   `scripts\gpu_remote.ps1 -Off` + reinicio para volver al local.

---

## Camino B — automatizado (API de RunPod, opcional)

`scripts/runpod_gpu.py` arranca y para una caja por script:
1. Cuenta de RunPod con crédito y **API key** → `RUNPOD_API_KEY` en `.env`.
2. (Opcional) un **Network Volume** (~20 GB) para que el modelo persista entre arranques; pásalo con
   `--volume <id>`.
3. `.venv\Scripts\python.exe -m pip install runpod`
4. `python scripts/runpod_gpu.py start` → arranca Ollama en la GPU, baja el modelo y escribe el `.env`.
5. Reinicia CallForge.
6. `python scripts/runpod_gpu.py stop` cuando termines (corta el cobro; el volumen se conserva).

Es un andamio honesto: revisa la GPU, el disco y la cuenta antes de correrlo en serio.

---

## Modelos sugeridos para la caja

- `mistral-small:24b` — buen español y JSON estable; maestro del dataset de reembolsos.
- `qwen3:30b-a3b` — MoE, rápido en una tarjeta de 24 GB.
- `llama3.3:70b` — en una A6000 de 48 GB, para comparar contra el especialista afinado.

Todos con licencia abierta y publicados en la biblioteca oficial de Ollama.
