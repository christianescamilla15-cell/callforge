"""Comparador de paridad (N08b): multiconjunto de filas con llave estable, huellas e inversiones.

* La comparación es por MULTICONJUNTO (`Counter` de filas), nunca posicional: el orden en que se
  exportan las filas no importa y una fila repetida cuenta dos veces. Lo que no empata se agrupa por
  la llave (conversación, turno) para decir QUÉ campo difiere, qué fila falta y cuál sobra.
* El orden que sí importa (los turnos de una conversación) se mide contando INVERSIONES, no por
  posición: una fila desplazada cuenta 1 inversión, no «todo distinto».
* La huella del lote replica la definición de `golden/generar_golden.py` sin importarlo.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field

COLUMNAS: tuple[str, ...] = (
    "conversacion", "turno", "agente", "ruta", "intent", "categoria", "urgencia", "escalada",
    "ticket_prioridad", "ticket_titulo", "estado", "paso", "con_resumen",
)
Fila = tuple[str, ...]


def normalizar(cruda: dict[str, str]) -> Fila:
    return tuple(str(cruda.get(c, "") or "").strip() for c in COLUMNAS)


def leer_csv(texto: str) -> list[Fila]:
    lector = csv.DictReader(io.StringIO(texto.lstrip("﻿")))
    faltan = set(COLUMNAS) - set(lector.fieldnames or ())
    if faltan:
        raise ValueError(f"al CSV le faltan columnas: {sorted(faltan)}")
    return [normalizar(f) for f in lector]


def llave(fila: Fila) -> tuple[str, str]:
    return fila[0], fila[1]


@dataclass
class Diferencias:
    faltantes: list[tuple[str, str]] = field(default_factory=list)      # en el esperado, no en lo obtenido
    sobrantes: list[tuple[str, str]] = field(default_factory=list)      # obtenidas que nadie esperaba
    distintas: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # llave -> campos que difieren

    @property
    def vacia(self) -> bool:
        return not (self.faltantes or self.sobrantes or self.distintas)

    def como_dict(self) -> dict:
        return {
            "faltantes": sorted("/".join(k) for k in self.faltantes),
            "sobrantes": sorted("/".join(k) for k in self.sobrantes),
            "distintas": {"/".join(k): sorted(v) for k, v in sorted(self.distintas.items())},
        }


def comparar(esperado: list[Fila], obtenido: list[Fila]) -> Diferencias:
    sobra_esperado = Counter(esperado) - Counter(obtenido)
    sobra_obtenido = Counter(obtenido) - Counter(esperado)
    por_llave_esp = {llave(f): f for f in sobra_esperado.elements()}
    por_llave_obt = {llave(f): f for f in sobra_obtenido.elements()}
    diferencias = Diferencias()
    for clave in sorted(set(por_llave_esp) | set(por_llave_obt)):
        esp, obt = por_llave_esp.get(clave), por_llave_obt.get(clave)
        if obt is None:
            diferencias.faltantes.append(clave)
        elif esp is None:
            diferencias.sobrantes.append(clave)
        else:
            diferencias.distintas[clave] = [c for c, a, b in zip(COLUMNAS, esp, obt, strict=True) if a != b]
    return diferencias


def inversiones(secuencia: list) -> int:
    """Parejas (i < j) en orden invertido: 0 = en orden; una fila desplazada k lugares cuenta k."""
    return sum(1 for i, a in enumerate(secuencia) for b in secuencia[i + 1:] if a > b)


def huella(fila: Fila) -> str:
    return hashlib.sha256("|".join(fila).encode("utf-8")).hexdigest()


def hash_lote(filas: list[Fila]) -> str:
    return hashlib.sha256("".join(sorted(huella(f) for f in filas)).encode("ascii")).hexdigest()
