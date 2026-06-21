"""Live check of the empathetic-companion persona with the real LLM."""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8902"
API = f"{BASE}/api/v1"
client = httpx.Client(timeout=120)


def turn(cid, text):
    return client.post(f"{API}/conversations/{cid}/message", json={"content": text}).json()


# 1) Emotional venting -> warm listening, no ticket-bot vibe
cid = client.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
r1 = turn(cid, "Me siento muy abrumado estos días, como que nada me sale bien.")
print(f"[desahogo] agent={r1['agent_used']} intent={r1['intent']} esc={r1['escalated']}")
print(f"  {r1['reply']}\n")

# 2) Wants a concrete calming technique -> troubleshooting (guided exercise)
r2 = turn(cid, "¿Me ayudas con algo para calmar la ansiedad ahorita?")
print(f"[técnica] agent={r2['agent_used']} esc={r2['escalated']}")
print(f"  {r2['reply']}\n")

# 3) Wants a human -> warm handoff
cid2 = client.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
r3 = turn(cid2, "Creo que necesito hablar con una persona de verdad.")
print(f"[humano] agent={r3['agent_used']} intent={r3['intent']} esc={r3['escalated']}")
print(f"  {r3['reply']}")
