# Runbook: fine-tune del agente de reembolsos (Qwen3-4B)

Pasos concretos para entrenar el especialista. Basado en las **grietas** que
encontró `scripts/refund_eval.py` en el baseline (4b local): decisión 82%,
JSON 86% (11 vacías), pide-info 4/11, tono repetido 86% ("Entiendo..."), y se
dobla ante presión social. El dataset y el entrenamiento atacan justo eso.

## Flujo (3 pasos)

### Paso 1 — Generar el dataset (desde tu PC, teacher fuerte en pod)
El generador necesita el paquete `callforge` (que está en tu PC) + un teacher
fuerte. NO uses el 4b local de teacher (genera datos pobres).

1. Levanta un pod con **Mistral-24B** (imagen `ollama/ollama`), expón 11434.
   En el Web Terminal: `ollama pull huihui_ai/mistral-small-abliterated:24b-instruct-2501-q4_K_M`
   (o el `mistral-small-instruct` vanilla — para datos de soporte no necesitas abliterado).
2. En tu PC, apunta CallForge al pod:
   `scripts\gpu_remote.ps1 -Url https://<pod>-11434.proxy.runpod.net -Model <mistral>`
3. Genera (~600 ejemplos; sube a 1000 si quieres):
   `.venv\Scripts\python.exe scripts\gen_refund_dataset.py --n 600 --out data/refund_dataset.jsonl`
4. Revisa la calidad: que las decisiones estén bien y las aperturas varíen
   (el generador ya descarta las que arrancan con "Entiendo"/mal-etiquetadas).
   Apaga el pod de Mistral cuando termines (`gpu_remote.ps1 -Off`).

### Paso 2 — Entrenar (en un pod GPU)
El entrenamiento es self-contained (NO necesita `callforge`, solo el JSONL + el script).

1. Levanta un pod con **imagen PyTorch/CUDA 12.x** (NO la de Ollama), GPU
   **A5000/A4000 (24/16GB basta para QLoRA de 4B)** — ~$0.25-0.27/hr.
2. Sube al pod: `data/refund_dataset.jsonl` y `scripts/finetune_refund.py`
   (Web Terminal: arrastra, o `wget`/`scp`, o pégalos).
3. Instala y entrena:
   ```bash
   pip install unsloth "trl<0.10" datasets
   python finetune_refund.py --data refund_dataset.jsonl --epochs 2
   ```
   (~10-30 min para 600 ej. en una A5000; cuesta centavos.)
4. Salida: `refund-agent-gguf/*.Q4_K_M.gguf` + `Modelfile`.

### Paso 3 — Deploy + eval (A/B vs baseline)
1. En el pod (o donde corras Ollama):
   `ollama create reembolsos -f Modelfile`
2. Apunta CallForge al modelo `reembolsos` y corre el test held-out:
   `.venv\Scripts\python.exe scripts\refund_eval.py`
3. Compara vs baseline. **Metas**: JSON 100%, decisión >95%, pide-info >9/11,
   tono-repetido <40%. Si no llega, más datos / más epochs / revisar ejemplos.

## Notas
- Base: `unsloth/Qwen3-4B-Instruct-2507` (vanilla Apache, NO abliterado — el
  reembolso no lo necesita y entrena más limpio).
- El `refund_eval.py` es el test held-out: NO entrenes con esos escenarios.
- Si el A/B no mejora lo suficiente, el problema casi siempre es el DATASET
  (diversidad/calidad), no los hiperparámetros. Itera el Paso 1.
- Alternativa sin entrenar: si corres un 24B de todas formas, mídelo con
  `refund_eval.py` primero — quizá ya pasa y te ahorras el fine-tune.
