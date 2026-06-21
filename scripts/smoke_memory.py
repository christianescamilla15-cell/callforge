"""Live check of companion memory: tell it facts across several turns,
trigger extraction, then in a NEW conversation see if it recalls."""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8907"
API = f"{BASE}/api/v1"
c = httpx.Client(timeout=120)


def say(cid, t):
    return c.post(f"{API}/conversations/{cid}/message", json={"content": t}).json()["reply"]


cid = c.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
# 6 customer turns -> triggers extraction (_MEMORY_EXTRACT_EVERY)
facts = [
    "Hola, me llamo Cris y vivo en la Ciudad de México.",
    "Me encanta el rock y el metal, sobre todo el dark punk.",
    "Estoy un poco estresado con un proyecto de software grande.",
    "Hago calistenia para despejarme.",
    "Tengo 28 años.",
    "Me interesa investigar artículos sobre la selva amazónica.",
]
for f in facts:
    say(cid, f)
print("6 turnos dichos -> extraccion de memoria disparada")

import time

time.sleep(1)
mem = c.get(f"{API}/companion/memories").json()
print(f"memorias guardadas: {mem['count']}")
for m in mem["memories"][:8]:
    print(f"  - {m['content']}")

# New conversation: does it remember?
cid2 = c.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
reply = say(cid2, "Oye, ¿te acuerdas de qué música me gusta?")
print(f"\nnueva conversacion, '¿qué música me gusta?':\n  {reply}")
