"""Post-cascade live smoke on :8001 (real Ollama): troubleshooting with
persistent steps + embeddings retrieval + scoped metrics."""
import time

import httpx

BASE = "http://127.0.0.1:8001/api/v1"
client = httpx.Client(timeout=300)

cid = client.post(f"{BASE}/conversations/start", json={}).json()["conversation_id"]

start = time.perf_counter()
m1 = client.post(
    f"{BASE}/conversations/{cid}/message",
    json={"content": "Mi internet no funciona, el modem tiene la luz roja"},
).json()
t1 = time.perf_counter() - start
print(f"MSG1 ({t1:.1f}s): agent={m1['agent_used']} escalated={m1['escalated']}")
print(f"  reply: {m1['reply'][:140]}")

m2 = client.post(
    f"{BASE}/conversations/{cid}/message",
    json={"content": "Ya reinicie el modem 60 segundos y la luz sigue roja"},
).json()
print(f"MSG2: agent={m2['agent_used']} escalated={m2['escalated']}")
print(f"  reply: {m2['reply'][:140]}")

detail = client.get(f"{BASE}/conversations/{cid}").json()
print(f"resolution_steps: {len(detail['resolution_steps'])}")
for s in detail["resolution_steps"]:
    print(f"  {s['step_number']}. [{s['status']}] {s['instruction'][:80]}")

metrics = client.get(f"{BASE}/metrics").json()
print(f"metrics tenant={metrics['tenant_id']} convs={metrics['conversations']['total']}")
