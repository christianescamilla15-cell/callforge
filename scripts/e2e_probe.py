"""Live E2E probe against a running CallForge instance (real LLM)."""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"

client = httpx.Client(timeout=300)

for doc in [
    {
        "title": "Como reiniciar el modem",
        "content": (
            "Si el internet no funciona: 1) Desconecta el modem de la corriente "
            "60 segundos. 2) Vuelve a conectarlo. 3) Espera a que la luz de "
            "internet quede fija en verde. Si tras esto sigue sin funcionar, "
            "se debe escalar a un tecnico."
        ),
        "tags": ["internet", "modem"],
    },
    {
        "title": "Politica de facturacion",
        "content": (
            "Las facturas se emiten el dia 1 de cada mes. Se pueden descargar "
            "del portal de clientes en la seccion Mis Facturas. Los pagos se "
            "pueden hacer hasta el dia 10 sin recargo."
        ),
        "tags": ["facturacion", "pagos"],
    },
]:
    client.post(f"{BASE}/knowledge/documents", json=doc).raise_for_status()

conv = client.post(f"{BASE}/conversations/start", json={"customer_name": "Ana"}).json()
cid = conv["conversation_id"]

scenarios = [
    ("TECH", "Mi internet no funciona desde ayer, el modem tiene una luz roja"),
    ("BILLING", "¿Hasta qué día puedo pagar mi factura sin recargo?"),
    ("HUMAN", "Nada de esto me sirve, quiero hablar con un humano ya"),
]

for label, content in scenarios:
    start = time.perf_counter()
    r = client.post(f"{BASE}/conversations/{cid}/message", json={"content": content})
    elapsed = time.perf_counter() - start
    body = r.json()
    print(f"--- {label} ({elapsed:.1f}s) ---")
    print(
        f"agent={body['agent_used']} intent={body['intent']} "
        f"urgency={body['urgency']} escalated={body['escalated']} "
        f"quality={body['quality_score']} conf={body['confidence']} "
        f"ticket={body['ticket_id']}"
    )
    print(f"REPLY: {body['reply']}\n")

metrics = client.get(f"{BASE}/metrics").json()
print("--- METRICS ---")
print(json.dumps(metrics["llm_usage"], indent=2))
print(
    f"convs={metrics['conversations']['total']} msgs={metrics['messages_total']} "
    f"runs={metrics['agent_runs_total']} escalations={metrics['escalations_total']} "
    f"errors={metrics['agent_errors_total']}"
)
sys.exit(0)
