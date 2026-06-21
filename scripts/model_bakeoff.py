"""Bake-off: candidate Ollama models vs current baseline, using CallForge's
real prompts (router JSON contract, KB-grounded support reply, escalation
customer_message). Measures warm latency and JSON contract adherence."""
from __future__ import annotations

import json
import sys
import time

import httpx

sys.path.insert(0, "src")
from callforge.agents.prompts import (  # noqa: E402
    ESCALATION_SYSTEM,
    ROUTER_SYSTEM,
    SUPPORT_SYSTEM,
)

OLLAMA = "http://localhost:11434"
MODELS = sys.argv[1:] or [
    "qwen2.5-coder:7b",  # baseline
    "qwen3.5:4b",
    "qwen3:4b-instruct-2507-q4_K_M",
    "granite4.1:3b",
]

KB_CONTEXT = (
    "KNOWLEDGE CONTEXT:\n[1] Politica de facturacion\nLas facturas se emiten el "
    "dia 1 de cada mes. Se pueden descargar del portal de clientes en la seccion "
    "Mis Facturas. Los pagos se pueden hacer hasta el dia 10 sin recargo."
)

CASES = [
    {
        "name": "router_tech",
        "system": ROUTER_SYSTEM,
        "user": "Customer message: Mi internet no funciona desde ayer, el modem tiene una luz roja",
        "require": ["intent", "next_agent", "urgency", "confidence"],
        "expect": {"intent": "technical_issue", "next_agent": "troubleshooting"},
    },
    {
        "name": "router_human",
        "system": ROUTER_SYSTEM,
        "user": "Customer message: Ya me harte, quiero hablar con un humano ahora mismo",
        "require": ["intent", "next_agent", "urgency", "confidence"],
        "expect": {"next_agent": "escalation"},
    },
    {
        "name": "support_kb_es",
        "system": SUPPORT_SYSTEM,
        "user": f"{KB_CONTEXT}\n\nCustomer message: Hasta que dia puedo pagar mi factura sin recargo?",
        "require": ["reply", "confidence"],
        "expect": {},
        "check_spanish_reply": True,
        "must_contain_any": ["10"],
    },
    {
        "name": "escalation_es",
        "system": ESCALATION_SYSTEM,
        "user": (
            'Routing classification: {"intent": "human_request", "urgency": "high"}\n\n'
            "Last customer message: Llevo tres dias sin servicio, quiero hablar con un humano\n\n"
            "Produce the escalation handoff package."
        ),
        "require": ["reason", "priority", "summary_for_human", "customer_message"],
        "expect": {},
        "check_spanish_field": "customer_message",
    },
]

SPANISH_HINTS = (
    "el ", "la ", "tu ", "de ", "que", "puede", "hasta", "caso", "agente",
    "factura", "dia", "día", "contactar", "escalado", "entiendo", "gracias",
)


def looks_spanish(text: str) -> bool:
    lowered = f" {text.lower()} "
    return sum(1 for w in SPANISH_HINTS if w in lowered) >= 2


def chat(client: httpx.Client, model: str, system: str, user: str) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3},
    }
    # Disable thinking on models that support it (fair latency comparison).
    started = time.perf_counter()
    response = client.post(f"{OLLAMA}/api/chat", json={**payload, "think": False})
    if response.status_code >= 400:
        response = client.post(f"{OLLAMA}/api/chat", json=payload)
    response.raise_for_status()
    elapsed = time.perf_counter() - started
    return response.json()["message"]["content"], elapsed


def evaluate(model: str, client: httpx.Client) -> dict:
    # Warm the model once (load to VRAM), excluded from timing.
    chat(client, model, "Reply with JSON.", 'Return {"ok": true}')

    passed = 0
    total_checks = 0
    latencies: list[float] = []
    notes: list[str] = []

    for case in CASES:
        text, elapsed = chat(client, model, case["system"], case["user"])
        latencies.append(elapsed)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            notes.append(f"{case['name']}: INVALID JSON")
            total_checks += 1
            continue

        ok = True
        total_checks += 1
        for key in case["require"]:
            if key not in data:
                ok = False
                notes.append(f"{case['name']}: missing key '{key}'")
        for key, expected in case["expect"].items():
            if str(data.get(key, "")).lower() != expected:
                ok = False
                notes.append(f"{case['name']}: {key}={data.get(key)!r} (expected {expected!r})")
        if case.get("check_spanish_reply") and not looks_spanish(str(data.get("reply", ""))):
            ok = False
            notes.append(f"{case['name']}: reply not in Spanish: {str(data.get('reply'))[:80]!r}")
        if case.get("must_contain_any"):
            reply = str(data.get("reply", ""))
            if not any(token in reply for token in case["must_contain_any"]):
                ok = False
                notes.append(f"{case['name']}: KB fact missing from reply: {reply[:80]!r}")
        field = case.get("check_spanish_field")
        if field:
            value = str(data.get(field, ""))
            if not looks_spanish(value):
                ok = False
                notes.append(f"{case['name']}: {field} not in Spanish: {value[:80]!r}")
            user_text = "llevo tres dias sin servicio"
            if user_text in value.lower():
                ok = False
                notes.append(f"{case['name']}: {field} echoes the customer")
        if ok:
            passed += 1

    # Free VRAM before the next model.
    client.post(
        f"{OLLAMA}/api/chat",
        json={"model": model, "messages": [], "keep_alive": 0},
    )
    return {
        "model": model,
        "passed": f"{passed}/{total_checks}",
        "avg_s": round(sum(latencies) / len(latencies), 2),
        "max_s": round(max(latencies), 2),
        "notes": notes,
    }


def main() -> None:
    client = httpx.Client(timeout=300)
    results = []
    for model in MODELS:
        print(f"=== {model} ===", flush=True)
        try:
            result = evaluate(model, client)
        except Exception as exc:  # noqa: BLE001
            result = {"model": model, "passed": "ERROR", "avg_s": None, "max_s": None, "notes": [str(exc)[:200]]}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"{r['model']:38} passed={r['passed']:6} avg={r['avg_s']}s max={r['max_s']}s")


if __name__ == "__main__":
    main()
