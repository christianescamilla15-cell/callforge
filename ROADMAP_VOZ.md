# Roadmap: Voz Humana en CallForge

Objetivo: que el asistente suene cada vez más humano — natural, con tu voz
clonada donde aporte, y con latencia conversacional — sin romper la regla
de oro del hardware: **los 6GB de VRAM son del LLM** (medido: Chatterbox
3.3-3.9GB + qwen3 ~3GB no caben juntos; Kokoro CPU RTF 0.34 sí convive).

Cada fase cierra con el pipeline (`scripts\ci.ps1`) en verde + validación
auditiva de Christian (la calidad de voz no se mide solo con tests).

---

## Fase 1 — Pulir la voz actual (Kokoro) · *quick wins, sin deps nuevas*

La naturalidad de Kokoro depende mucho de QUÉ texto le das, no solo del modelo.

- [x] **1.1 Normalización de texto pre-TTS** (`voice/normalize.py`): markdown/
  emojis/URLs fuera, números 0-999999 y horas a palabras, abreviaturas.
- [x] **1.2 A/B de voces es**: 9 muestras en `scripts/voice_ab/` (3 voces ×
  3 velocidades) — **pendiente solo tu elección al oído** → `.env`.
- [x] **1.3 Prosodia por puntuación**: 0.25s tras punto, 0.12s tras coma.
- [x] **1.4 Cache de TTS** (`CachedTTS` → `cache/tts/`): clave = texto
  normalizado + voz; frases repetidas en ~0ms.

**Sale cuando**: Christian apruebe la voz elegida al oído + pipeline verde.

## Fase 2 — Tu voz clonada donde no duele la latencia · *Chatterbox offline*

Chatterbox NO sirve para el turno en vivo en esta GPU (RTF 1.8-4.4), pero
sí para audio pregrabado con tu voz. Híbrido: **tu voz para lo fijo,
Kokoro para lo dinámico**.

- [x] **2.1 Voice inbox**: todo audio que mandas por el mic se persiste en
  `data/voice_inbox/` — las referencias se acumulan solas hablando con la
  app. (Los audios previos a esto se descartaban tras el STT.)
- [x] **2.2 Banco de frases clonadas**: `scripts/build_voice_bank.py`
  sintetiza las frases de `voice/phrases.py` con Chatterbox + tu referencia
  y siembra `cache/tts/` con la MISMA clave que usa el runtime.
- [x] **2.3 Servir el banco**: gratis — es el cache de 1.4; cero código de
  serving, sin restart.
- [ ] **2.4 PENDIENTE DE TI**: 15-20s de tu voz (hablarle al mic de la app
  ya las guarda en el inbox, o WAV directo a `voices/`) → corro el builder
  → validación round-trip + tu aprobación auditiva.

**Sale cuando**: el webchat saluda con TU voz y el pipeline sigue verde.

## Fase 3 — Latencia conversacional (TTFA < 2s) · *streaming por oraciones*

Hoy: el turno espera el reply completo del LLM y luego sintetiza todo.
Meta: la primera oración suena mientras el resto aún se genera.

- [x] **3.2 TTS incremental (pipeline de oraciones)**: el turno de voz con
  `?stream=true` devuelve el audio de la PRIMERA oración de inmediato +
  las restantes como texto; el cliente las pide y encola gapless en el
  AudioContext mientras suena la primera. El toggle de altavoz usa el
  mismo pipeline. TTFA del audio ≈ una oración (~0.5-1s tras el reply).
- [ ] **3.1 LLM streaming** (DECISIÓN PENDIENTE de Christian): hoy el
  cuello es el LLM (4-13s para producir el reply completo porque el
  contrato de agentes es JSON). Para TTFA total <2s hay que streamear el
  LLM, y eso implica cambiar el contrato: el agente respondería texto
  plano streameado + un segundo pase/función para metadata (confianza,
  escalación). Afecta a los 6 agentes y al QualityAgent. Beneficio: voz
  que empieza a hablar mientras el LLM piensa. Costo: refactor del
  workflow + reevaluar el retry de calidad. Decidir antes de implementar.
- [ ] **3.3 WS de audio binario**: mover el turno de voz completo al
  WebSocket (audio del mic como frame binario, chunks de respuesta
  empujados por el server). Hace innecesario el fetch por oración.

**Sale cuando**: TTFA medido < 2s en el webchat con Groq.

## Fase 4 — Naturalidad de siguiente nivel · *plan post-research GitHub (2026-06-12, 22 claims verificados)*

Orden por ratio ganancia/esfuerzo en ESTE hardware:

- [x] **4.1 Fillers conversacionales**: "Mmm, déjame revisar..." suena al
  INSTANTE de soltar el mic, enmascarando los 5-15s del LLM (prefetch al
  primer gesto, rotación aleatoria, gap de 0.3s antes del reply real).
- [x] **4.2 Anti-clicks + loudness**: fades de 4ms en bordes + pico a
  -1.5 dBFS en Kokoro. DSP pesado solo si gana el A/B auditivo.
- [ ] **4.3 Banco expresivo es-MX**: regenerar el banco con el finetune
  `ResembleAI/Chatterbox-Multilingual-es-mx-latam` (MIT, abr-2026) +
  sweep `exaggeration 0.5-0.8 / cfg_weight 0.3-0.5` (knobs documentados
  para habla expresiva). Bloqueado por: 15-20s de voz de Christian.
- [ ] **4.4 A/B DeepFilterNet — DIFERIDO**: `pip install deepfilternet`
  exige toolchain Rust en Windows (sin wheel py3.12) y su ganancia sobre
  TTS limpio es dudosa (es supresor de ruido). Reevaluar si publican wheel
  o vía WSL. resemble-enhance también descartado en Windows (arrastra
  deepspeed). ClearerVoice (Apache, SR 24k→48k) queda como candidato para
  el banco offline cuando exista.
- [x] **4.5 CAPA VC EN VIVO — FUNCIONA (medido 2026-06-12)**: OpenVoice V2
  (MIT) convirtiendo la salida de Kokoro al timbre de Christian, TODO en
  CPU: converter aislado RTF 0.30-0.37; cadena E2E Kokoro→/convert RTF
  0.84 en caliente (~24s el primer call por cargas lazy). Inteligibilidad
  verbatim (round-trip Whisper). Implementado: `/convert` en voice_server
  (embeddings precalculados de `voices/christian_full.wav`) + engine
  `ClonedLiveTTS` (TTS_ENGINE=kokoro_cloned) con degradación automática a
  Kokoro plano si el converter no responde. seed-vc descartado (GPL-3.0,
  sin ventaja al ya tener CPU real-time MIT). Activación: aprobación
  auditiva de Christian → flip en .env + voice server como servicio.
- **Descartado con evidencia**: Chatterbox-Turbo es solo inglés (los tags
  [laugh] también) — no hay camino a Chatterbox es en vivo en 6GB; AudioSR
  en vivo (RTF 1.6 en una A6000); Orpheus 3B en vivo (sí sirve para
  generar fillers/risas es offline: tags <laugh>/<sigh>, Apache el ft es_it).
- **Referencia arquitectónica**: `KoljaB/RealtimeVoiceChat` (MIT, 3.8k⭐) =
  el análogo directo de CallForge (WS→STT→Ollama→TTS con Kokoro); leer su
  código antes de la Fase 5 (turn-taking/barge-in).

## Fase 5 — Voz full-duplex / telefonía · *horizonte producto*

- VAD server-side + barge-in (interrumpir al bot hablando), turn-taking
  natural, y el canal telefónico (Twilio/SIP) sobre los mismos use cases —
  la arquitectura de canales ya lo permite (patrón `routes/webchat.py`).

---

## Decisiones ya tomadas (no reabrir sin datos nuevos)

| Decisión | Evidencia |
|---|---|
| Kokoro = voz en vivo | RTF 0.34 CPU, 0 VRAM, español OK (round-trip Whisper verbatim) |
| Chatterbox = solo offline | 3.3-3.9GB VRAM + RTF 1.8-4.4 en la 4050 (medido 2026-06-11) |
| STT = Groq Whisper | $0.04/hr, 0.9s, transcripción es verbatim |
| F5-TTS descartado | CC-BY-NC — ilegal para producto comercial |
| Fish S2 Pro descartado | Licencia de pago + 4.4B no cabe |
