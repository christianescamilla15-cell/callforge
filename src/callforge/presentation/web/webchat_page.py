"""Minimal webchat page with voice support (mic input + spoken replies).
Kept as a Python string so it ships inside the wheel without package-data
configuration. Replace with a real frontend later."""

WEBCHAT_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CallForge - Soporte</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6;
         display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 16px; background:#161a22; border-bottom:1px solid #232a36;
           font-weight:600; display:flex; justify-content:space-between; align-items:center; }
  header small { color:#8a93a3; font-weight:400; }
  #log { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:78%; padding:10px 14px; border-radius:14px; line-height:1.4; white-space:pre-wrap; }
  .me { align-self:flex-end; background:#2b5cd9; border-bottom-right-radius:4px; }
  .bot { align-self:flex-start; background:#1c222d; border-bottom-left-radius:4px; }
  .meta { font-size:11px; color:#8a93a3; margin-top:4px; }
  .escalated { color:#ffb454; }
  form { display:flex; gap:8px; padding:12px 16px; background:#161a22; border-top:1px solid #232a36; }
  input { flex:1; padding:11px 14px; border-radius:10px; border:1px solid #2a3342;
          background:#0f1115; color:#e6e6e6; font-size:15px; }
  button { padding:11px 16px; border-radius:10px; border:0; background:#2b5cd9; color:#fff;
           font-weight:600; cursor:pointer; font-size:15px; }
  button:disabled { opacity:.5; }
  .icon { background:#1c222d; border:1px solid #2a3342; min-width:48px; }
  .icon.active { background:#7a2330; border-color:#c2455a; animation:pulse 1.2s infinite; }
  .icon.on { background:#1d4a2c; border-color:#3f9c5c; }
  @keyframes pulse { 50% { opacity:.6; } }
  .replay { display:inline-block; margin-top:6px; cursor:pointer; opacity:.55;
            font-size:15px; user-select:none; }
  .replay:hover { opacity:1; }
</style>
</head>
<body>
<header>CallForge<small id="status">conectando...</small></header>
<div id="log"></div>
<form id="form">
  <button type="button" id="mic" class="icon" title="Hablar (clic para grabar / enviar)">&#127908;</button>
  <button type="button" id="handsfree" class="icon" title="Modo manos libres: el mic escucha solo y puedes interrumpir al asistente">&#128222;</button>
  <button type="button" id="speak" class="icon" title="Leer respuestas en voz alta">&#128264;</button>
  <select id="voiceSel" class="icon" title="Voz del asistente" style="max-width:120px;color:#e6e6e6;"></select>
  <button type="button" id="addVoice" class="icon" title="Clonar una voz nueva (sube 10-30s de audio)">&#10133;</button>
  <input type="file" id="voiceFile" accept="audio/*" style="display:none">
  <input id="input" placeholder="Escribe tu mensaje..." autocomplete="off" maxlength="8000">
  <button id="send" disabled>Enviar</button>
</form>
<script>
const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const send = document.getElementById("send");
const mic = document.getElementById("mic");
const speakBtn = document.getElementById("speak");
const status = document.getElementById("status");

const params = new URLSearchParams(location.search);
const token = params.get("token");
const headers = token ? { "X-API-Token": token } : {};
let conversationId = null;
let speakReplies = true;  // voz ON por defecto (el 1er clic/mensaje desbloquea el audio del navegador)
let recorder = null;
let chunks = [];
let streamBubble = null;  // incremental bot bubble during streaming

function add(text, cls, meta, audioB64) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  if (meta) {
    const m = document.createElement("div");
    m.className = "meta" + (meta.escalated ? " escalated" : "");
    m.textContent = meta.escalated
      ? "Escalado a agente humano - ticket " + (meta.ticket_id || "")
      : "agente: " + meta.agent_used;
    div.appendChild(m);
  }
  if (cls === "bot") {
    // Replay button: replays cached audio, or re-synthesizes in the chosen voice.
    const replay = document.createElement("div");
    replay.className = "replay";
    replay.innerHTML = "&#128266;";
    replay.title = "Reproducir de nuevo";
    replay.onclick = () => replaySpeak(text);  // full text, not the partial cached clip
    div.appendChild(replay);
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// --- Audio playback ---
// Chrome blocks .play() once the user-gesture window expires (our replies
// arrive seconds after the click). An AudioContext resumed during ANY user
// gesture stays unlocked forever, so all replies route through it.
let audioCtx = null;
function unlockAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  }
  if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
}

// Gapless queue: each WAV chunk is scheduled right after the previous one,
// so sentence-by-sentence audio sounds like a single continuous reply.
let queueTime = 0;
let activeSources = [];  // scheduled/playing sources, so barge-in can stop them

function trackSource(source) {
  activeSources.push(source);
  source.onended = () => {
    const i = activeSources.indexOf(source);
    if (i >= 0) activeSources.splice(i, 1);
  };
}

// Barge-in: stop everything the bot is saying right now.
function stopPlayback() {
  for (const s of activeSources.slice()) {
    try { s.stop(); } catch (e) { /* already stopped */ }
  }
  activeSources = [];
  queueTime = audioCtx ? audioCtx.currentTime : 0;
}

function botIsSpeaking() {
  return audioCtx && queueTime > audioCtx.currentTime + 0.05;
}

async function playWav(arrayBuffer) {
  try {
    unlockAudio();
    const buffer = await audioCtx.decodeAudioData(arrayBuffer);
    const startAt = Math.max(audioCtx.currentTime, queueTime);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    source.start(startAt);
    trackSource(source);
    queueTime = startAt + buffer.duration;
  } catch (err) {
    // Fallback: may still be blocked by autoplay policy, but we tried.
    const blob = new Blob([arrayBuffer], { type: "audio/wav" });
    new Audio(URL.createObjectURL(blob)).play().catch(() => {});
  }
}

// A persona can pin its own voice (set via PERSONA_VOICES); the server sends it
// in reply_done. Falls back to the voice picked in the UI.
let personaVoice = "";
async function fetchAndQueueTts(text) {
  const res = await fetch("/api/v1/voice/tts", {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice: (personaVoice || selectedVoice()) || null }),
  });
  if (res.ok) await playWav(await res.arrayBuffer());
}

// --- Conversational fillers: short "thinking" sounds played the instant a
// voice turn starts, masking LLM latency. Prefetched after the first gesture.
let fillerBuffers = [];
let fillersLoaded = false;
async function prefetchFillers() {
  if (fillersLoaded) return;
  fillersLoaded = true;
  try {
    unlockAudio();
    const texts = await (await fetch("/api/v1/voice/fillers", { headers })).json();
    for (const text of texts) {
      const res = await fetch("/api/v1/voice/tts", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice: selectedVoice() || null }),
      });
      if (res.ok) {
        fillerBuffers.push(await audioCtx.decodeAudioData(await res.arrayBuffer()));
      }
    }
  } catch (err) { /* fillers are a nicety */ }
}

function playFiller() {
  if (!fillerBuffers.length || !audioCtx) return;
  const buffer = fillerBuffers[Math.floor(Math.random() * fillerBuffers.length)];
  const startAt = Math.max(audioCtx.currentTime, queueTime);
  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);
  source.start(startAt);
  trackSource(source);
  queueTime = startAt + buffer.duration + 0.3;  // breath before the real reply
}

function playB64(b64) {
  if (!b64) return;
  const raw = atob(b64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
  playWav(bytes.buffer);
}

async function speakText(text) {
  if (!speakReplies || !text) return;
  await replaySpeak(text);
}

// Explicit replay: speak the WHOLE text, sentence by sentence (gapless),
// regardless of the auto-speak toggle. Never plays a partial cached clip.
async function replaySpeak(text) {
  if (!text) return;
  unlockAudio();
  const sentences = text.split(/(?<=[.!?…])\s+/).filter(s => s.trim());
  try {
    for (const sentence of (sentences.length ? sentences : [text])) {
      await fetchAndQueueTts(sentence);
    }
  } catch (err) { /* la voz es best-effort */ }
}

// --- WebSocket with auto-reconnect + conversation resume ---
let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECT = 6;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const extra = conversationId ? "&conversation=" + conversationId : "";
  const qs = location.search ? location.search + extra
                             : (extra ? "?" + extra.slice(1) : "");
  ws = new WebSocket(proto + "://" + location.host + "/webchat/ws" + qs);

  ws.onopen = () => {
    status.textContent = "en linea";
    send.disabled = false;
    reconnectAttempts = 0;
  };
  ws.onclose = () => {
    send.disabled = true;
    if (reconnectAttempts >= MAX_RECONNECT) {
      status.textContent = "desconectado - recarga la pagina";
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 15000);
    reconnectAttempts += 1;
    status.textContent = "reconectando (" + reconnectAttempts + ")...";
    setTimeout(connect, delay);
  };
  ws.onerror = () => { /* onclose follows and handles retry */ };
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "session") {
      const isNew = !data.resumed;
      conversationId = data.conversation_id;
      if (isNew && log.childElementCount === 0 && !data.opener) {
        // No server-side contextual opener -> show the default greeting.
        add("Hola, qué bueno que estás aquí. Tómate tu tiempo y cuéntame cómo te sientes hoy.", "bot");
      }
    } else if (data.type === "reply") {
      add(data.reply, "bot", data);
      if (speakReplies) speakText(data.reply);
      send.disabled = false;
    } else if (data.type === "reply_chunk") {
      // Streaming: build the bubble incrementally, speak each sentence now.
      if (!streamBubble) {
        streamBubble = document.createElement("div");
        streamBubble.className = "msg bot";
        log.appendChild(streamBubble);
      }
      streamBubble.textContent += (streamBubble.textContent ? " " : "") + data.text;
      log.scrollTop = log.scrollHeight;
      if (speakReplies) fetchAndQueueTts(data.text);
    } else if (data.type === "reply_done") {
      if ("voice" in data) personaVoice = data.voice || "";  // persona's pinned voice
      if (streamBubble) {
        const replay = document.createElement("div");
        replay.className = "replay";
        replay.innerHTML = "&#128266;";
        replay.title = "Reproducir de nuevo";
        const full = data.reply || streamBubble.textContent;
        replay.onclick = () => replaySpeak(full);  // full reply, gapless
        streamBubble.appendChild(replay);
        streamBubble = null;
      }
      send.disabled = false;
    } else if (data.type === "error") {
      streamBubble = null;
      add("Hubo un problema procesando tu mensaje. Intenta de nuevo.", "bot");
      send.disabled = false;
    }
  };
}
connect();

form.onsubmit = (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== 1) return;
  add(text, "me");
  ws.send(text);
  input.value = "";
  send.disabled = true;
};

speakBtn.onclick = () => {
  unlockAudio();
  speakReplies = !speakReplies;
  speakBtn.classList.toggle("on", speakReplies);
  speakBtn.innerHTML = speakReplies ? "&#128266;" : "&#128264;";
};

// --- Voice cloning panel: pick the assistant's voice, upload new ones ---
const voiceSel = document.getElementById("voiceSel");
const addVoice = document.getElementById("addVoice");
const voiceFile = document.getElementById("voiceFile");

function selectedVoice() {
  return voiceSel.value || "";
}

let voiceEngine = "local";
async function loadVoices() {
  try {
    const data = await (await fetch("/api/v1/voice/voices", { headers })).json();
    voiceEngine = data.engine || "local";
    const saved = localStorage.getItem("cloneVoice_" + voiceEngine) || data.default || "";
    voiceSel.innerHTML = "";
    const optDefault = document.createElement("option");
    optDefault.value = "";
    optDefault.textContent = "voz: default";
    voiceSel.appendChild(optDefault);
    for (const v of data.voices || []) {
      const id = typeof v === "string" ? v : v.id;
      const label = typeof v === "string" ? v : v.name;
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = "voz: " + label;
      if (id === saved) opt.selected = true;
      voiceSel.appendChild(opt);
    }
    // Cloning works in both engines now (Cartesia cloud + local OpenVoice).
    addVoice.style.display = "";
    addVoice.title = voiceEngine === "cartesia"
      ? "Clonar una voz nueva en Cartesia (sube 10-60s de audio limpio)"
      : "Clonar una voz nueva (sube 10-30s de audio)";
  } catch (err) { /* selector queda en default */ }
}
loadVoices();

voiceSel.onchange = () => {
  localStorage.setItem("cloneVoice_" + voiceEngine, voiceSel.value);
  fillerBuffers = []; fillersLoaded = false;  // refetch fillers in new voice
};

addVoice.onclick = () => voiceFile.click();
voiceFile.onchange = async () => {
  const file = voiceFile.files[0];
  if (!file) return;
  const name = (window.prompt("Nombre para esta voz (a-z, 0-9, _ o -):") || "")
    .trim().toLowerCase();
  if (!name) return;
  status.textContent = "clonando voz...";
  const formData = new FormData();
  formData.append("file", file, file.name);
  try {
    const res = await fetch("/api/v1/voice/voices?name=" + encodeURIComponent(name), {
      method: "POST", headers, body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      add("No se pudo clonar la voz: " + (err.detail || res.status), "bot");
    } else {
      const data = await res.json();
      const newId = data.id || name;  // Cartesia returns a voice id
      await loadVoices();
      voiceSel.value = newId;
      localStorage.setItem("cloneVoice_" + voiceEngine, newId);
      fillerBuffers = []; fillersLoaded = false;
      add("Voz '" + (data.name || name) + "' lista. Ya puedo hablar con ella.", "bot");
    }
  } catch (err) {
    add("Error de red subiendo la voz.", "bot");
  } finally {
    status.textContent = "en linea";
    voiceFile.value = "";
  }
};

async function sendVoice(blob) {
  if (!conversationId) return;
  status.textContent = "transcribiendo...";
  const formData = new FormData();
  formData.append("file", blob, "voice.webm");
  try {
    const voiceQ = selectedVoice() ? "&voice=" + encodeURIComponent(selectedVoice()) : "";
    const res = await fetch(
      "/api/v1/conversations/" + conversationId + "/voice-message?stream=true" + voiceQ,
      { method: "POST", headers, body: formData }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      add(err.detail === "Could not transcribe audio"
        ? "No pude entender el audio, intenta de nuevo."
        : "Error procesando el audio.", "bot");
      return;
    }
    const data = await res.json();
    add(data.transcript, "me");
    add(data.reply, "bot", data, data.audio_b64);
    playB64(data.audio_b64);  // first sentence, immediate
    for (const sentence of (data.remaining_sentences || [])) {
      await fetchAndQueueTts(sentence);  // queues gapless behind it
    }
  } catch (err) {
    add("Error de red enviando el audio.", "bot");
  } finally {
    status.textContent = "en linea";
  }
}

mic.onclick = async () => {
  unlockAudio();
  prefetchFillers();
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    add("El navegador no expone el microfono en este origen (se requiere HTTPS o http://localhost). Abriste la app via " + location.origin + ".", "bot");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      mic.classList.remove("active");
      const blob = new Blob(chunks, { type: "audio/webm" });
      if (blob.size > 1000) {
        playFiller();  // instant "thinking" sound while the LLM works
        sendVoice(blob);
      }
    };
    recorder.start();
    mic.classList.add("active");
    status.textContent = "grabando... (clic en el mic para enviar)";
  } catch (err) {
    add("No se pudo acceder al microfono. Revisa permisos del navegador.", "bot");
  }
};

// --- Hands-free / full-duplex: the mic listens continuously, detects when
// you speak (energy VAD), interrupts the assistant's audio (barge-in), and
// sends your turn on silence. Echo cancellation keeps the bot's own voice
// from triggering it. Toggle with the phone button.
const handsfree = document.getElementById("handsfree");
let hf = { on: false, stream: null, analyser: null, data: null, rec: null,
           chunks: [], state: "listening", lastVoice: 0, speechStart: 0, timer: null };
const HF_SPEECH_RMS = 0.020;   // above this = you are talking
const HF_SILENCE_MS = 900;     // this much silence ends your turn
const HF_MIN_SPEECH_MS = 280;  // ignore shorter blips

function hfRms() {
  hf.analyser.getFloatTimeDomainData(hf.data);
  let sum = 0;
  for (let i = 0; i < hf.data.length; i++) sum += hf.data[i] * hf.data[i];
  return Math.sqrt(sum / hf.data.length);
}

function hfLoop() {
  if (!hf.on) return;
  const rms = hfRms();
  const now = performance.now();
  if (hf.state === "listening") {
    if (rms > HF_SPEECH_RMS) {
      if (botIsSpeaking()) stopPlayback();   // barge-in
      hf.chunks = [];
      hf.rec.start();
      hf.state = "recording";
      hf.speechStart = now;
      hf.lastVoice = now;
      mic.classList.add("active");
      status.textContent = "te escucho...";
    }
  } else if (hf.state === "recording") {
    if (rms > HF_SPEECH_RMS) hf.lastVoice = now;
    if (now - hf.lastVoice > HF_SILENCE_MS) {
      const spoke = hf.lastVoice - hf.speechStart;
      hf.state = "listening";
      mic.classList.remove("active");
      if (hf.rec.state === "recording") hf.rec.stop();  // onstop decides to send
      hf._spoke = spoke;
    }
  }
}

async function startHandsFree() {
  unlockAudio();
  prefetchFillers();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    add("El microfono requiere HTTPS o http://localhost.", "bot");
    return;
  }
  try {
    hf.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    add("No se pudo acceder al microfono.", "bot");
    return;
  }
  const src = audioCtx.createMediaStreamSource(hf.stream);
  hf.analyser = audioCtx.createAnalyser();
  hf.analyser.fftSize = 1024;
  hf.data = new Float32Array(hf.analyser.fftSize);
  src.connect(hf.analyser);  // analyser only, NOT to destination (no echo)
  hf.rec = new MediaRecorder(hf.stream, { mimeType: "audio/webm" });
  hf.rec.ondataavailable = (e) => hf.chunks.push(e.data);
  hf.rec.onstop = () => {
    const blob = new Blob(hf.chunks, { type: "audio/webm" });
    if ((hf._spoke || 0) >= HF_MIN_SPEECH_MS && blob.size > 1500) {
      playFiller();
      sendVoice(blob);
    }
  };
  hf.on = true;
  hf.state = "listening";
  hf.timer = setInterval(hfLoop, 50);
  handsfree.classList.add("on");
  status.textContent = "manos libres: habla cuando quieras";
}

function stopHandsFree() {
  hf.on = false;
  if (hf.timer) clearInterval(hf.timer);
  if (hf.rec && hf.rec.state === "recording") hf.rec.stop();
  if (hf.stream) hf.stream.getTracks().forEach((t) => t.stop());
  handsfree.classList.remove("on");
  mic.classList.remove("active");
  status.textContent = "en linea";
}

handsfree.onclick = () => { hf.on ? stopHandsFree() : startHandsFree(); };
</script>
</body>
</html>
"""
