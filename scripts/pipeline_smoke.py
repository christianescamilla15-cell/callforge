"""Pipeline smoke: end-to-end probes of every public surface against a live
instance. Exits non-zero on the first failure. Used by scripts/ci.ps1."""
from __future__ import annotations

import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8901"
API = f"{BASE_URL}/api/v1"
client = httpx.Client(timeout=300)

PASSED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name} {detail}")
        sys.exit(1)


print(f"[smoke] target {BASE_URL}")

# 1. Health
health = client.get(f"{API}/health").json()
check("health", health["status"] == "ok", str(health))

# 2. Knowledge ingest
doc = client.post(
    f"{API}/knowledge/documents",
    json={
        "title": "Politica de facturacion",
        "content": "Los pagos se pueden hacer hasta el dia 10 sin recargo.",
        "tags": ["billing"],
    },
)
check("knowledge ingest", doc.status_code == 201)

# 3. Conversation lifecycle
cid = client.post(f"{API}/conversations/start", json={"customer_name": "CI"}).json()[
    "conversation_id"
]
check("start conversation", bool(cid))

t0 = time.perf_counter()
msg = client.post(
    f"{API}/conversations/{cid}/message",
    json={"content": "Hasta que dia puedo pagar mi factura sin recargo?"},
).json()
check(
    "text message turn",
    bool(msg["reply"]) and not msg["escalated"],
    f"agent={msg['agent_used']}",
)
print(f"      ({time.perf_counter() - t0:.1f}s, agent={msg['agent_used']})")

listed = client.get(f"{API}/conversations?limit=5").json()
check("list conversations", any(c["id"] == cid for c in listed))

detail = client.get(f"{API}/conversations/{cid}").json()
check("conversation detail", len(detail["messages"]) == 2)

# 4. Escalation -> ticket -> ticket management
cid2 = client.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
esc = client.post(
    f"{API}/conversations/{cid2}/message",
    json={"content": "Quiero hablar con un humano ahora mismo"},
).json()
check("escalation creates ticket", esc["escalated"] and esc["ticket_id"])

post_esc = client.post(
    f"{API}/conversations/{cid2}/message", json={"content": "sigo esperando"}
).json()
check("escalated guard (no bot re-run)", post_esc["agent_used"] == "escalation")

patched = client.patch(
    f"{API}/tickets/{esc['ticket_id']}", json={"status": "in_progress"}
).json()
check("ticket status update", patched["status"] == "in_progress")

# 5. Close + feedback
closed = client.post(f"{API}/conversations/{cid}/close", json={"resolved": True}).json()
check("close conversation", closed["status"] == "resolved")
fb = client.post(
    f"{API}/feedback", json={"conversation_id": cid, "rating": 5, "resolved": True}
)
check("feedback", fb.status_code == 201)

# 6. Voice (TTS always; STT only if the instance has a Groq key)
tts = client.post(f"{API}/voice/tts", json={"text": "Prueba de voz del pipeline."})
if tts.status_code == 200:
    check("voice tts", tts.content[:4] == b"RIFF", f"{len(tts.content)} bytes")
else:
    check("voice tts (disabled is acceptable)", tts.status_code == 503)

# 7. Pages + metrics
check("webchat page", client.get(f"{BASE_URL}/webchat").status_code == 200)
check("dashboard page", client.get(f"{BASE_URL}/dashboard").status_code == 200)
metrics = client.get(f"{API}/metrics").json()
check("metrics", metrics["conversations"]["total"] >= 2)

print(f"[smoke] {PASSED} checks passed")
