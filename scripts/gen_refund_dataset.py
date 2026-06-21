"""Fase 1: synthetic dataset generator for the refund specialist.

Uses a TEACHER model (point .env at a STRONG one — e.g. Mistral-24B on a pod)
to generate diverse angry-customer refund scenarios WITH the ideal RefundAgent
output, label-correct (decision pinned per category). Targets the cracks the eval
found: holding the line under pressure, asking for missing info, threshold
precision, and TONE VARIETY (forbids the templated 'Entiendo...' opener).
Writes ChatML/messages JSONL ready for Unsloth.

Run (on the pod, teacher loaded):
  .venv\\Scripts\\python.exe scripts\\gen_refund_dataset.py --n 600 --out data/refund_dataset.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

from callforge.agents.prompts import REFUND_AGENT_SYSTEM
from callforge.config import get_settings
from callforge.infrastructure.llm.fallback import build_provider_chain

ROOT = Path(__file__).resolve().parent.parent
POLICY = (ROOT / "data" / "refund_policy.md").read_text(encoding="utf-8")

# decision + descripción + flags que atacan las grietas del eval.
# hold_line: el cliente presiona; el agente NO se dobla. ask: el reply DEBE pedir
# el dato que falta. Distribución sesgada hacia los casos difíciles.
CATEGORIES = [
    {"d": "aprobar", "desc": "producto defectuoso o dañado dentro de 90 días"},
    {"d": "aprobar", "desc": "artículo equivocado / no es lo que pidió, dentro de 90 días"},
    {"d": "aprobar", "desc": "pedido no entregado y pasaron >10 días hábiles de la fecha estimada"},
    {"d": "aprobar", "desc": "cambio de opinión dentro de 15 días, producto sin usar"},
    {"d": "aprobar", "desc": "defecto claro pero el cliente está MUY agresivo/grosero (de-escalar y aprobar)"},
    {"d": "negar", "desc": "fuera de ventana (>90 días) sin defecto, solo ya no le gusta"},
    {"d": "negar", "desc": "insatisfacción subjetiva fuera de la ventana de 15 días"},
    {"d": "negar", "desc": "fuera de política PERO el cliente presiona (amigo del dueño, VIP, amenaza redes/demanda/contracargo, súplica)", "hold_line": True},
    {"d": "negar", "desc": "fuera de política pero el cliente lo pide MUY educado/amable", "hold_line": True},
    {"d": "escalar", "desc": "reembolso ya aprobado pero no recibido (>7 días hábiles)"},
    {"d": "escalar", "desc": "sin recibo ni número de pedido, no se puede verificar la compra", "ask": True},
    {"d": "escalar", "desc": "info incompleta (no recuerda fecha/pedido); hay que pedirla", "ask": True},
    {"d": "escalar", "desc": "señales de fraude (múltiples reembolsos, datos que no cuadran, cuenta de tercero)"},
    {"d": "escalar", "desc": "cliente dice que 'otro agente ya le aprobó' sin comprobante", "hold_line": True, "ask": True},
    {"d": "escalar", "desc": "defecto justo FUERA de la ventana de 90 días (caso borde, requiere supervisor)"},
]

TONES = ["furioso con groserías", "sarcástico", "amenazante", "cansado pero firme",
         "exige supervisor", "educado pero molesto", "EN MAYÚSCULAS", "pasivo-agresivo"]
PRODUCTS = ["una licuadora", "unos audífonos", "unos tenis", "una plancha", "un celular",
            "una cafetera", "una mochila", "un monitor", "un colchón", "una aspiradora"]
# Aperturas variadas para MATAR la repetición de "Entiendo..." (la grieta de tono).
OPENERS = ["ve directo al grano sin preámbulo", "reconoce el problema con una frase distinta",
           "empieza con una disculpa breve", "valida con humor ligero y humano",
           "arranca nombrando el siguiente paso", "abre con una pregunta concreta"]


def _gen_prompt(c: dict, tone: str, product: str, opener: str, seed: int) -> str:
    extra = ""
    if c.get("hold_line"):
        extra += " El cliente INTENTA presionarte (presión social, amenaza o súplica) pero MANTIENES la decisión con firmeza y empatía, sin doblarte ni acusar."
    if c.get("ask"):
        extra += " En el `reply` DEBES pedir el dato que falta (número de pedido o comprobante) — incluye una pregunta concreta."
    return (
        f"Eres un generador de datos de entrenamiento para un agente de reembolsos. "
        f"Inventa UN ejemplo realista y ÚNICO (variación #{seed}).\n"
        f"Escenario: {c['desc']}.\nProducto: {product}.\nTono del cliente: {tone}.\n"
        f"La decisión correcta SEGÚN POLÍTICA es: {c['d']}.{extra}\n"
        f"IMPORTANTE para el `reply`: {opener}. PROHIBIDO empezar con 'Entiendo' o "
        f"'Lamento'; varía la apertura. Español mexicano natural, de-escalador, breve.\n\n"
        f"Devuelve SOLO este JSON:\n"
        f'{{"customer": "<mensaje del cliente molesto, natural y variado>", '
        f'"reply": "<respuesta del agente>", "decision": "{c["d"]}", '
        f'"reason": "<por qué, citando la regla>", "refund_amount": <número o null>, '
        f'"ticket": {{"category": "refund|delivery|defect|fraud|other", "priority": "low|medium|high|urgent", '
        f'"summary": "<breve>", "customer_request": "<lo que pide>"}}}}'
    )


async def run(n: int, out: Path) -> None:
    llm = build_provider_chain(get_settings())
    print(f"teacher: {[p.name for p in llm.providers]} -> {out}")
    written = 0
    dropped = 0
    with out.open("w", encoding="utf-8") as f:
        for i in range(n):
            c = CATEGORIES[i % len(CATEGORIES)]
            tone = TONES[(i // len(CATEGORIES)) % len(TONES)]
            product = PRODUCTS[i % len(PRODUCTS)]
            opener = OPENERS[i % len(OPENERS)]
            try:
                res = await llm.complete(
                    _gen_prompt(c, tone, product, opener, i),
                    [{"role": "user", "content": "Genera el ejemplo."}],
                    json_mode=True,
                )
                ex = json.loads(res.text)
                customer = (ex.pop("customer", "") or "").strip()
                reply = (ex.get("reply", "") or "").strip()
                # Quality gates: label-correct, has customer+reply, asks when required,
                # and NOT the templated opener (the tone crack).
                if not customer or not reply or ex.get("decision", "").lower() != c["d"]:
                    dropped += 1; continue
                if c.get("ask") and "?" not in reply:
                    dropped += 1; continue
                if reply.lower().startswith(("entiendo", "lamento", "comprendo")):
                    dropped += 1; continue
                user = f"POLÍTICA DE REEMBOLSOS:\n{POLICY}\n\nCLIENTE:\n{customer}"
                record = {"messages": [
                    {"role": "system", "content": REFUND_AGENT_SYSTEM},
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": json.dumps(ex, ensure_ascii=False)},
                ]}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
                if written % 25 == 0:
                    print(f"  {written} escritos ({dropped} descartados)")
            except Exception:  # noqa: BLE001 - skip a bad generation, keep going
                dropped += 1
                continue
    print(f"listo: {written} ejemplos -> {out}  ({dropped} descartados por calidad)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=600)
    p.add_argument("--out", default="data/refund_dataset.jsonl")
    a = p.parse_args()
    random.seed(0)
    asyncio.run(run(a.n, ROOT / a.out))
