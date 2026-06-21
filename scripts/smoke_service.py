"""Post-deploy smoke: one escalation in Spanish against the installed service."""
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
client = httpx.Client(timeout=300)

cid = client.post(f"{BASE}/conversations/start", json={"customer_name": "Luis"}).json()[
    "conversation_id"
]
start = time.perf_counter()
body = client.post(
    f"{BASE}/conversations/{cid}/message",
    json={"content": "Llevo tres dias sin servicio, quiero hablar con un humano ahora"},
).json()
elapsed = time.perf_counter() - start
print(f"({elapsed:.1f}s) escalated={body['escalated']} ticket={body['ticket_id']}")
print(f"REPLY: {body['reply']}")
