"""Live voice E2E: real Groq STT + real Kokoro TTS through the full turn."""
import base64
import time

import httpx

BASE = "http://127.0.0.1:8001/api/v1"
client = httpx.Client(timeout=300)

cid = client.post(f"{BASE}/conversations/start", json={}).json()["conversation_id"]

with open("scripts/test_stt.wav", "rb") as f:
    audio = f.read()

start = time.perf_counter()
response = client.post(
    f"{BASE}/conversations/{cid}/voice-message",
    files={"file": ("voice.wav", audio, "audio/wav")},
)
elapsed = time.perf_counter() - start
body = response.json()
print(f"({elapsed:.1f}s) status={response.status_code}")
print(f"TRANSCRIPT: {body['transcript']}")
print(f"AGENT: {body['agent_used']} | escalated: {body['escalated']}")
print(f"REPLY: {body['reply'][:150]}")
wav = base64.b64decode(body["audio_b64"]) if body["audio_b64"] else b""
print(f"AUDIO: {len(wav)} bytes, RIFF={wav[:4] == b'RIFF'}")
with open("scripts/test_voice_reply.wav", "wb") as f:
    f.write(wav)

start = time.perf_counter()
tts = client.post(f"{BASE}/voice/tts", json={"text": "Tu caso fue registrado correctamente."})
print(f"/voice/tts: {tts.status_code}, {len(tts.content)} bytes en {time.perf_counter()-start:.1f}s")
