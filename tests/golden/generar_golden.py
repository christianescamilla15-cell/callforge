#!/usr/bin/env python3
"""Oráculo independiente del lote golden de conversaciones (N08b).

    python3 tests/golden/generar_golden.py            # reescribe esperado.csv y cifras_control.json
    python3 tests/golden/generar_golden.py --revisar  # sale con 1 si lo versionado no está al día

NO importa `callforge`: deriva el resultado esperado de cada turno leyendo las reglas tal como
están escritas en docs/reglas.md (secciones «Enrutamiento», «Escalamiento», «Ciclo de vida» y
«Diagnóstico guiado») y del contrato del modo sin LLM (MockProvider). Si el código y este guion
discrepan, la prueba de paridad lo dice fila por fila y alguien decide si es mejora o defecto.

Entrada: conversaciones/entrada.json. Salida: conversaciones/esperado.csv (una fila por turno),
conversaciones/cifras_control.json (conteos por lote y hash del lote) y conversaciones/control_negativo/
(un esperado y una entrada alterados, con las diferencias exactas que la prueba debe encontrar). El
consumo de tokens no se deriva aquí (depende del texto exacto de los prompts): vive aparte, aprobado,
en consumo_aprobado.json.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

LOTE = Path(__file__).resolve().parent / "conversaciones"

COLUMNAS = ("conversacion", "turno", "agente", "ruta", "intent", "categoria", "urgencia", "escalada",
            "ticket_prioridad", "ticket_titulo", "estado", "paso", "con_resumen")

# docs/reglas.md · señales del modo sin LLM, en orden de precedencia (humano > técnico > facturación).
SENALES = (
    ("humano", ("humano", "human", "supervisor", "gerente", "manager", "agente real", "persona real",
                "hablar con alguien")),
    ("tecnico", ("error", "no funciona", "not working", "falla", "fails", "crash", "internet", "modem",
                 "router", "lento", "slow", "no carga", "broken", "se cae", "intermitente")),
    ("facturacion", ("factura", "facturacion", "cobro", "cargo", "billing", "charge", "refund", "reembolso",
                     "pago", "payment", "invoice")),
)
CLASIFICACION = {   # señal -> (intent, categoría, urgencia, agente siguiente)
    "humano": ("human_request", "escalation", "high", "escalation"),
    "tecnico": ("technical_issue", "technical", "medium", "troubleshooting"),
    "facturacion": ("billing", "billing", "medium", "support"),
    "general": ("question", "general", "low", "support"),
}
PRIORIDAD_SIN_LLM = "high"   # el EscalationAgent determinista siempre propone «high»


def _senal(mensaje: str) -> str:
    texto = mensaje.lower()
    return next((nombre for nombre, palabras in SENALES if any(p in texto for p in palabras)), "general")


def turnos(conversacion: dict) -> list[dict[str, str]]:
    """Filas esperadas de una conversación, turno por turno."""
    filas: list[dict[str, str]] = []
    escalada, pasos, resumen = False, 0, False
    clasificacion = ("", "", "")
    for numero, mensaje in enumerate(conversacion["mensajes"], start=1):
        fila = {"conversacion": conversacion["clave"], "turno": str(numero), "ticket_prioridad": "",
                "ticket_titulo": "", "paso": ""}
        if escalada:
            # Una conversación escalada es del humano: se guarda el mensaje, el bot no corre y
            # la clasificación es la que ya tenía.
            intent, categoria, urgencia = clasificacion
            fila.update(agente="escalation", ruta="", intent=intent, categoria=categoria, urgencia=urgencia,
                        escalada="si")
        else:
            intent, categoria, urgencia, siguiente = CLASIFICACION[_senal(mensaje)]
            clasificacion = (intent, categoria, urgencia)
            fila.update(intent=intent, categoria=categoria, urgencia=urgencia)
            if siguiente == "escalation":
                escalada, resumen = True, True
                fila.update(agente="router", ruta="router>escalation>summarizer", escalada="si",
                            ticket_prioridad=PRIORIDAD_SIN_LLM, ticket_titulo=f"[{categoria}] {mensaje[:80]}")
            else:
                fila.update(agente=siguiente, ruta=f"router>{siguiente}>quality", escalada="no")
                if siguiente == "troubleshooting":
                    pasos += 1
                    fila["paso"] = str(pasos)
        fila["estado"] = "escalated" if escalada else "active"
        fila["con_resumen"] = "si" if resumen else "no"
        filas.append(fila)
    return filas


def cifras(conversaciones: list[dict], filas: list[dict[str, str]]) -> dict:
    """Cifras control del lote: lo que el sistema debe haber persistido, contado por separado."""
    corridas: Counter[str] = Counter()
    eventos: Counter[str] = Counter()
    mensajes = 0
    for fila in filas:
        agentes = [a for a in fila["ruta"].split(">") if a]
        corridas.update(agentes)
        if not agentes:                       # aviso de conversación escalada: sólo el mensaje del cliente
            mensajes += 1
            continue
        mensajes += 2
        eventos["routed"] += 1
        if "escalation" in agentes:
            eventos["escalated"] += 1
        else:
            eventos["knowledge_retrieved"] += 1
            eventos["quality_checked"] += 1
    ultimas = {f["conversacion"]: f for f in filas}
    pasos = sum(1 for f in filas if f["paso"])
    # Todo paso propuesto queda «answered» si la conversación tuvo otro turno después.
    respondidos = sum(1 for f in filas if f["paso"] and int(f["turno"]) < len(
        next(c for c in conversaciones if c["clave"] == f["conversacion"])["mensajes"]))
    return {
        "conversaciones": len(conversaciones),
        "turnos": len(filas),
        "mensajes": mensajes,
        "corridas_por_agente": dict(sorted(corridas.items())),
        "corridas_total": sum(corridas.values()),
        "eventos_por_tipo": dict(sorted(eventos.items())),
        "tickets_por_prioridad": dict(Counter(f["ticket_prioridad"] for f in filas if f["ticket_prioridad"])),
        "estado_final": dict(sorted(Counter(f["estado"] for f in ultimas.values()).items())),
        "pasos": {"total": pasos, "answered": respondidos, "proposed": pasos - respondidos},
        "hash_lote": hash_lote(filas),
        "definicion_hash": "sha256 de las huellas sha256 por fila (campos de COLUMNAS unidos por «|»), "
                           "ordenadas: no depende del orden de las filas",
    }


def huella(fila: dict[str, str]) -> str:
    return hashlib.sha256("|".join(fila[c] for c in COLUMNAS).encode("utf-8")).hexdigest()


def hash_lote(filas: list[dict[str, str]]) -> str:
    return hashlib.sha256("".join(sorted(huella(f) for f in filas)).encode("ascii")).hexdigest()


# --------------------------------------------------------------------------- #
# Control negativo: diferencias SEMBRADAS que la prueba de paridad debe encontrar, exactas.
# --------------------------------------------------------------------------- #

# 1) Un esperado alterado a mano (como si el oráculo se hubiera equivocado o el sistema cambiara).
ALTERACIONES_ESPERADO = {
    "cambia_ruta": ("C02", "2", {"agente": "support", "ruta": "router>support>quality"}),
    "voltea_escalada": ("C05", "2", {"escalada": "no", "estado": "active"}),
    "solo_espacios": ("C06", "1", {"categoria": "  billing  "}),   # la normalización no lo cuenta
}
QUITA_FILA = ("C04", "2")                       # el sistema la produce y el esperado ya no: «sobrante»
FILA_INVENTADA = {c: "" for c in COLUMNAS} | {  # el esperado la pide y nadie la produce: «faltante»
    "conversacion": "C99", "turno": "1", "agente": "support", "ruta": "router>support>quality",
    "intent": "question", "categoria": "general", "urgencia": "low", "escalada": "no",
    "estado": "active", "con_resumen": "no"}

# 2) Una entrada alterada: el mensaje de C03 ahora pide un gerente (facturación → humano).
MENSAJE_ALTERADO = ("C03", 0, "Quiero hablar con un gerente sobre el cargo de mi factura")


def _csv(filas: list[dict[str, str]]) -> str:
    salida = io.StringIO()
    escritor = csv.DictWriter(salida, fieldnames=COLUMNAS, lineterminator="\n")
    escritor.writeheader()
    escritor.writerows(filas)
    return salida.getvalue()


def _por_llave(filas: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {f"{f['conversacion']}/{f['turno']}": f for f in filas}


def control_negativo(conversaciones: list[dict], filas: list[dict[str, str]]) -> dict[str, str]:
    alterado = []
    for fila in filas:
        if (fila["conversacion"], fila["turno"]) == QUITA_FILA:
            continue
        fila = dict(fila)
        for clave, turno, cambios in ALTERACIONES_ESPERADO.values():
            if (fila["conversacion"], fila["turno"]) == (clave, turno):
                fila.update(cambios)
        alterado.append(fila)
    alterado.append(FILA_INVENTADA)

    entrada_alterada = json.loads(json.dumps({"conversaciones": conversaciones}))
    clave, indice, texto = MENSAJE_ALTERADO
    next(c for c in entrada_alterada["conversaciones"] if c["clave"] == clave)["mensajes"][indice] = texto
    filas_alteradas = [f for c in entrada_alterada["conversaciones"] for f in turnos(c)]
    normales, otras = _por_llave(filas), _por_llave(filas_alteradas)
    distintas_entrada = {k: sorted(c for c in COLUMNAS if normales[k][c] != otras[k][c])
                         for k in normales if normales[k] != otras[k]}

    esperadas = {
        "esperado_alterado": {
            "faltantes": ["/".join(FILA_INVENTADA[c] for c in ("conversacion", "turno"))],
            "sobrantes": ["/".join(QUITA_FILA)],
            "distintas": {f"{c}/{t}": sorted(cambios) for nombre, (c, t, cambios)
                          in ALTERACIONES_ESPERADO.items() if nombre != "solo_espacios"},
        },
        "entrada_alterada": {"faltantes": [], "sobrantes": [], "distintas": distintas_entrada},
    }
    return {
        "control_negativo/esperado_alterado.csv": _csv(alterado),
        "control_negativo/entrada_alterada.json":
            json.dumps(entrada_alterada, indent=2, ensure_ascii=False) + "\n",
        "control_negativo/alteraciones.json": json.dumps(esperadas, indent=2, ensure_ascii=False) + "\n",
    }


def construir() -> dict[str, str]:
    entrada = json.loads((LOTE / "entrada.json").read_text(encoding="utf-8"))
    conversaciones = entrada["conversaciones"]
    filas = [fila for c in conversaciones for fila in turnos(c)]
    return {
        "esperado.csv": _csv(filas),
        "cifras_control.json": json.dumps(cifras(conversaciones, filas), indent=2, ensure_ascii=False) + "\n",
        **control_negativo(conversaciones, filas),
    }


def main() -> int:
    archivos = construir()
    if "--revisar" in sys.argv[1:]:
        viejos = [n for n, t in archivos.items()
                  if not (LOTE / n).is_file() or (LOTE / n).read_text(encoding="utf-8") != t]
        print("golden al día" if not viejos else f"DESACTUALIZADO: {', '.join(viejos)} (corre sin --revisar)")
        return 1 if viejos else 0
    for nombre, texto in archivos.items():
        (LOTE / nombre).parent.mkdir(parents=True, exist_ok=True)
        (LOTE / nombre).write_text(texto, encoding="utf-8")
        print(f"escrito {LOTE.name}/{nombre}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
