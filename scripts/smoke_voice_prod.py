"""Final production smoke on :8000 - voice turn + Groq as primary LLM."""
import base64
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
client = httpx.Client(timeout=300)

cid = client.post(f"{BASE}/conversations/start", json={}).json()["conversation_id"]

with open("scripts/test_stt.wav", "rb") as f:
    audio = f.read()

start = time.perf_counter()
body = client.post(
    f"{BASE}/conversations/{cid}/voice-message",
    files={"file": ("voice.wav", audio, "audio/wav")},
).json()
elapsed = time.perf_counter() - start
print(f"({elapsed:.1f}s) transcript: {body['transcript'][:80]}")
print(f"agent={body['agent_used']} escalated={body['escalated']}")
print(f"REPLY: {body['reply'][:160]}")
wav = base64.b64decode(body["audio_b64"]) if body.get("audio_b64") else b""
print(f"audio hablado: {len(wav)} bytes RIFF={wav[:4] == b'RIFF'}")

metrics = client.get(f"{BASE}/metrics").json()
for u in metrics["llm_usage"]:
    print(f"provider={u['provider']} calls={u['calls']} cost=${u['estimated_cost_usd']}")
