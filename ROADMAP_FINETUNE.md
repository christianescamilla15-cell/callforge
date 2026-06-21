# Roadmap: Agente especialista en reembolsos (fine-tuning)

Especializar un modelo en **atención a clientes molestos que exigen su reembolso**:
de-escalar el enojo, decidir según política (aprobar / negar / escalar), generar el
ticket. Uno de los nichos de mayor volumen y fricción en un call center.

## Dos verdades antes de entrenar

1. **El mejor candidato a fine-tunear es Qwen3-4B-Instruct, no el más grande.** Para
   una tarea ESTRECHA, un 4B afinado le gana a un 70B genérico: barato de entrenar,
   corre en la 6GB local, Apache-2.0, y ya fue el ganador del bake-off de CallForge en
   los contratos JSON. Especialización > tamaño.
2. **Fine-tune NO es el primer paso.** Un buen system prompt del agente de reembolsos +
   RAG sobre la política (ya existe `HybridKnowledgeStore`) da ~80% sin entrenar. Se
   fine-tunea cuando eso se estanca: para consistencia a tamaño 4B, menor costo/latencia,
   o matices que el prompt no fija. **Baseline prompt+RAG primero, medir, luego entrenar.**

## Fases

### Fase 0 — Especificación
- Intents/escenarios: pedido no llegó, defectuoso, artículo equivocado, reembolso
  tardío, fuera de política, sospecha de fraude, sin recibo.
- Política de reembolso (reglas de decisión): cuándo aprueba/niega/escala, montos, plazos.
- Schema del ticket (campos exactos).
- Tono: de-escalación (validar -> contener -> resolver), sin moralizar.
- Contratos: reusar los JSON de CallForge.

### Fase 1 — Dataset (70% del éxito)
- Sin datos reales con PII -> **generación sintética con modelo maestro** (Mistral-24B
  local o frontier en nube): 500-2000 diálogos cliente-enojado <-> agente, multi-turno,
  variados, cada uno con la salida IDEAL en el formato de los contratos.
- Calidad > cantidad: diversidad + casos borde (fraude, sin recibo, fuera de política).
- Formato: plantilla chat Qwen (system+user+assistant con JSON), split train/val/test.

### Fase 2 — Modelo + método
- Modelo: `Qwen3-4B-Instruct-2507` (Apache, corre local). Opción mayor: Mistral-24B.
- Método: **QLoRA con Unsloth** (2x más rápido, menos VRAM, exporta a GGUF para Ollama).

### Fase 3 — Entrenamiento (sobre RunPod)
- 4B QLoRA cabe en A4000/A5000 ($0.25-0.27/hr); unas horas = un par de dólares.
- Script Unsloth: Qwen3-4B -> LoRA -> entrenar -> guardar adaptadores.

### Fase 4 — Deploy (pipeline actual)
- Merge LoRA -> GGUF q4 -> `ollama create reembolsos-agent -f Modelfile` -> corre como
  cualquier otro modelo. Cero arquitectura nueva.

### Fase 5 — Evaluación (extiende ci.ps1)
- Test set held-out: % JSON válido, decisión correcta vs política, campos del ticket,
  tono/de-escalación (juez = modelo), escalación correcta. A/B fine-tuned vs base.
- `scripts/refund_eval.py` es el arnés inicial (baseline + eval).

### Fase 6 — Integración + A/B
- Cablear el especialista como modelo/agente en CallForge (modo soporte), A/B vs base.

### Fase 7 — Iterar
- Trazas de producción (`agent_runs`) -> refinar dataset -> re-entrenar.

## Costo
- Dataset: gratis (generado). Entrenamiento: ~$2-5 en RunPod. Barato.

## Artefactos en el repo
- `ROADMAP_FINETUNE.md` (este).
- `src/callforge/agents/prompts.py` -> `REFUND_AGENT_SYSTEM` (Fase 0/baseline).
- `data/refund_policy.md` (política para RAG/contexto).
- `scripts/refund_eval.py` (baseline + eval; semilla del dataset).
- `scripts/gen_refund_dataset.py` (Fase 1: generador sintético).
