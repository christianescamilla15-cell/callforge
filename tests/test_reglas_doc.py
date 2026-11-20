"""docs/reglas.md es la compuerta «una prueba por regla»: esta prueba evita que la tabla mienta.

Cada `tests/...py::prueba` citado debe existir como función de prueba de nivel módulo, sin marca
skip/xfail, y cada fila de regla debe citar al menos una.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOC = RAIZ / "docs" / "reglas.md"
_REF = re.compile(r"`(tests/[\w/]+\.py)::(test_\w+)`")
_FILA_REGLA = re.compile(r"^\|\s*([A-Z]\d+)\s*\|")


def _filas() -> list[tuple[str, str]]:
    return [(m.group(1), linea) for linea in DOC.read_text(encoding="utf-8").splitlines()
            if (m := _FILA_REGLA.match(linea))]


def _pruebas_de(archivo: str) -> dict[str, bool]:
    """{nombre: ¿se salta?} de las funciones de prueba de nivel módulo."""
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    pruebas = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name.startswith("test_"):
            marcas = {ast.unparse(d) for d in nodo.decorator_list}
            pruebas[nodo.name] = any(".skip" in m or "xfail" in m for m in marcas)
    return pruebas


def test_la_tabla_tiene_todas_sus_reglas():
    identificadores = [ident for ident, _ in _filas()]
    assert len(identificadores) >= 70, "la tabla perdió reglas"
    assert len(identificadores) == len(set(identificadores)), "hay identificadores de regla repetidos"


def test_cada_regla_cita_al_menos_una_prueba():
    assert [ident for ident, linea in _filas() if not _REF.search(linea)] == []


def test_cada_prueba_citada_existe_y_no_se_salta():
    faltantes, saltadas = [], []
    for archivo, prueba in _REF.findall(DOC.read_text(encoding="utf-8")):
        existentes = _pruebas_de(archivo) if (RAIZ / archivo).is_file() else {}
        if prueba not in existentes:
            faltantes.append(f"{archivo}::{prueba}")
        elif existentes[prueba]:
            saltadas.append(f"{archivo}::{prueba}")
    assert faltantes == []
    assert saltadas == []
