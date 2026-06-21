"""Live check of companion streaming over the WebSocket: measure time to the
FIRST sentence vs the full reply, with the real local model."""
import time

from websockets.sync.client import connect

import sys

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8906/webchat/ws"

with connect(URL) as ws:
    import json

    json.loads(ws.recv())  # session
    ws.send("Cuentame como estuvo tu dia, con detalle, sin prisa.")
    start = time.perf_counter()
    first = None
    chunks = []
    while True:
        msg = json.loads(ws.recv())
        if msg["type"] == "reply_chunk":
            if first is None:
                first = time.perf_counter() - start
                print(f"primera frase en {first:.1f}s: {msg['text'][:70]}")
            chunks.append(msg["text"])
        elif msg["type"] == "reply_done":
            total = time.perf_counter() - start
            print(f"respuesta completa en {total:.1f}s ({len(chunks)} frases)")
            print(f"ahorro: la voz arranca {total - first:.1f}s antes que sin streaming")
            break
