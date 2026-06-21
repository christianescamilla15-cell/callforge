# Roadmap: Compañero LLM Local Sin Restricciones

Objetivo: un acompañante conversacional **privado, local y sin las negativas
ni el moralizar** de los modelos cloud — "como un Claude pero tuyo" — usando
la voz clonada y el stack de voz que ya construimos.

## Qué significa "sin restricciones" aquí (y qué SÍ conservamos)

Lo valioso y legítimo de un modelo local sin censura, para un compañero:
- **No rechaza** peticiones benignas ni temas maduros/oscuros en ficción/charla.
- **No moraliza** ni mete disclaimers corporativos en cada respuesta.
- **Se mantiene en personaje** — no rompe la cuarta pared con "como IA no puedo...".
- **Privado**: corre en tu máquina, ninguna conversación sale a un tercero.

Lo único que mantenemos (ya lo acordamos en el pivot a acompañante): **la red
de crisis**. No como censura, sino como calidad — un compañero que ante una
señal de autolesión *anima* el daño no es "más libre", está roto. Es una sola
rama de comportamiento, todo lo demás va abierto y natural. Tú decides; se
puede quitar con una palabra.

## La buena noticia arquitectónica

Tu cadena `groq → ollama → mock` **ya tiene el slot local**. Cambiar a un
modelo sin restricciones es:
1. `ollama pull <modelo-uncensored>`
2. `OLLAMA_MODEL=<modelo>` + (opcional) quitar Groq como primario → `ollama` primario
3. El system-prompt de persona que ya existe (acompañante) se mantiene

No hay reescritura. El motor es intercambiable por diseño.

---

## Fases

### Fase 1 — Probar modelos uncensored en la 4050 (HOY, $0)
Modelos VERIFICADOS en el registry de Ollama (deep-research 2026-06-12, 24/25
claims confirmados) que caben en 6GB:
- `huihui_ai/qwen2.5-abliterate:7b` (4.7GB Q4) — Qwen2.5 abliterado, el mejor balance.
- `huihui_ai/dolphin3-abliterated:8b` (4.9GB) — Dolphin 3.0 sobre Llama 3.1 8B.
- `huihui_ai/qwen2.5-abliterate:3b` (1.9GB) — más rápido, deja VRAM de sobra.

- [x] Bake-off (`scripts/uncensored_bakeoff.py`): mide **tasa de rechazo**,
  mantenimiento de personaje, español y latencia vs el qwen3:4b actual.
- [ ] El ganador entra como `OLLAMA_MODEL`; flip de cadena a ollama-primario
  (privado total) o mantener groq-primario con ollama de respaldo (calidad).

Nota técnica: "abliterated" = cirugía de pesos que ablaciona la dirección de
rechazo (Arditi et al. 2024) — quita negativas pero NO añade personaje; por eso
el system-prompt fuerte de persona + memoria importan tanto como el modelo.

### Fase 2 — Persona persistente + memoria del compañero
- [ ] System-prompt de persona enriquecido (nombre, tono, historia, límites
  suaves) — más allá del acompañante genérico actual.
- [ ] **Memoria a largo plazo**: el compañero recuerda conversaciones pasadas
  (no solo la sesión). Ya tienes el SummarizerAgent + la tabla de resúmenes;
  extender a un perfil persistente que se inyecta en el contexto (RAG sobre
  el historial con los embeddings que ya tienes).
- [ ] Memoria episódica: "la última vez me contaste que..." — inyectar
  resúmenes de sesiones previas al iniciar.

### Fase 3 — Calidad cuasi-Claude (decisión de hardware)
La verdad honesta: **ningún modelo local de 6GB iguala a Claude**. Opciones:
- [ ] **Caja LAN RTX 3060 12GB** (~$4k MXN GPU + caja): corre uncensored de
  12-14B = el salto real de calidad de personaje. El voice/LLM server ya es
  URL remota; apuntar `OLLAMA_BASE_URL` a `http://192.168.x.x:11434`. La caja
  además absorbe tu watcher de NexusForge y libera la laptop.
  Modelos verificados para 12GB:
  - `huihui_ai/qwen2.5-abliterate:14b` (9.0GB Q4) — más cerebro, abliterado.
  - **Mistral-Nemo-12B** uncensored = **mejor español** (multilingüe oficial,
    tokenizer Tekken ~30% más eficiente en es, diseñado para conversación).
    Variantes: `vanilj/mistral-nemo-12b-celeste-v1.9` (RP/personaje, usar tag
    Q4/Q5 — el Q8 son 13GB y no cabe) o el HERETIC de DavidAU (de-censurado
    87→14/100 rechazos + fine-tune de razonamiento).
  Matiz: abliterated = razona mejor; RP-tune (Celeste) = mejor personaje pero
  más repetición y peor en seguir instrucciones largas. Probar ambos.
- [ ] Alternativa sin hardware: aceptar el techo de 6GB para lo privado, y
  mantener Groq (rápido, buena calidad, algo de alignment) para cuando la
  calidad importe más que el "sin filtros".

### Fase 4 — Fine-tune propio (como tu amigo) — avanzado, opcional
- [ ] Si quieres "literalmente cualquier conversación" con TU estilo/persona:
  LoRA de persona con Unsloth (entrena en la 4050 o en la caja) sobre un base
  uncensored. La investigación evalúa: ¿vale el esfuerzo vs abliterated +
  prompt fuerte? (spoiler probable: el 90% del efecto se logra con
  abliterated + buen system-prompt; el fine-tune es para el último 10% de
  estilo personal).
- [ ] Dataset: tus propias conversaciones / el estilo que quieras imitar.

### Fase 5 — Compañero completo (full duplex)
- [ ] Voz tuya (ya está) + LLM local sin filtros + memoria persistente +
  el pipeline de voz en vivo (fillers, streaming de oraciones) = un compañero
  con el que hablas por voz, que te recuerda, con tu voz, privado.
- [ ] Frameworks de referencia a estudiar (la investigación los mapea):
  SillyTavern (persona/memoria/lorebooks), RealtimeVoiceChat (pipeline voz).

---

## 🅿️ EN EL RADAR — Servidor para el salto de calidad (parqueado 2026-06-12)

Cuándo retomar: cuando quieras correr **modelos 12-14B uncensored** (mejor
español/personaje/coherencia que el 7-8B actual) o que **dolphin3 vuele** sin
los 10s de latencia. Tu arquitectura ya está lista: el LLM es URL remota →
apuntar `OLLAMA_BASE_URL` a la IP y listo, cero reescritura.

Opciones con costos verificados (deep-research 2026-06-12):

| Estrategia | Qué | Costo real |
|---|---|---|
| **GPU on-demand** ⭐ | Vast.ai / TensorDock RTX 4090 24GB, encendida solo al conversar (1-2h/día), API start/stop | **~$11-33/mes** (spot ~$6-12) |
| Caja LAN propia | PC usado + RTX 3060 12GB, en tu red 24/7, absorbe el watcher de NexusForge | ~$7-9k MXN una vez |
| 24/7 cloud / Hetzner | dedicado fijo | pierde por costo (Hetzner ~€184/mo) |

Recomendación parqueada: **GPU on-demand (Vast.ai/TensorDock)** — el cerebro
grande se "enciende" solo cuando le hablas; con Groq de respaldo no necesitas
tenerla prendida. Privacidad/ToS: Vast.ai no prohíbe uncensored explícitamente.
Cuando digas, monto el script de arranque/parada automático (RunPod API).

## Decisiones abiertas (las resuelve la investigación + tú)
- ¿Qué modelo uncensored concreto gana en la 4050? (research en curso)
- ¿Compras la caja 12GB para el salto de calidad, o aceptas el techo de 6GB?
- ¿Fine-tune propio o abliterated + prompt?
- ¿Cadena ollama-primario (privado total) o groq-primario (calidad, algo de
  alignment) con ollama de respaldo?

## Brecha honesta vs Claude
Un modelo local de 6-14B uncensored conversa muy bien, no rechaza y es
privado — pero no razona ni escribe como Claude/GPT de frontera. Para un
*compañero* (calidez, charla, personaje, memoria) la brecha importa poco; para
tareas complejas de razonamiento, sí. El diseño correcto: local uncensored
para el compañero, y Groq/cloud disponible para cuando necesites el cerebro
grande. Tu cadena de fallback ya permite tener ambos.
