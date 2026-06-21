"""Post-restart smoke on the production service (:8000): confirm the new
model is serving and the cascade features respond."""
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
client = httpx.Client(timeout=300)

cid = client.post(f"{BASE}/conversations/start", json={}).json()["conversation_id"]

start = time.perf_counter()
m1 = client.post(
    f"{BASE}/conversations/{cid}/message",
    json={"content": "Mi internet va lentisimo desde la manana"},
).json()
t1 = time.perf_counter() - start
print(f"MSG1 ({t1:.1f}s, incl. carga del modelo): agent={m1['agent_used']}")

start = time.perf_counter()
m2 = client.post(
    f"{BASE}/conversations/{cid}/message",
    json={"content": "Ya hice eso y sigue lento"},
).json()
t2 = time.perf_counter() - start
print(f"MSG2 ({t2:.1f}s, caliente): agent={m2['agent_used']}")
print(f"  reply: {m2['reply'][:120]}")

detail = client.get(f"{BASE}/conversations/{cid}").json()
print(f"resolution_steps: {len(detail['resolution_steps'])}")

metrics = client.get(f"{BASE}/metrics").json()
ollama = next((u for u in metrics["llm_usage"] if u["provider"] == "ollama"), None)
print(f"metrics: tenant={metrics['tenant_id']} ollama_calls={ollama['calls'] if ollama else 0}")
