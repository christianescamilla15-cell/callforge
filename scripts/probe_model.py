"""Quick companion probe against an ad-hoc instance: does the model give ONE
clean reply, or hallucinate a fake multi-turn dialogue?"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8908"
API = f"{BASE}/api/v1"
c = httpx.Client(timeout=120)
cid = c.post(f"{API}/conversations/start", json={}).json()["conversation_id"]
for t in ["que onda, estoy medio bajoneado hoy", "pss no se, cosas del trabajo"]:
    t0 = time.perf_counter()
    r = c.post(f"{API}/conversations/{cid}/message", json={"content": t}).json()
    dt = time.perf_counter() - t0
    reply = r["reply"]
    # heuristics for hallucinated dialogue
    lines = [l for l in reply.split("\n") if l.strip()]
    fake = len(lines) > 3
    print(f"--- ({dt:.1f}s){'  HALLUCINA DIALOGO' if fake else ''}")
    print(f"  {reply}\n")
