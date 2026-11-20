"""Contrato OpenAPI (N07): versionado en docs/api/openapi.json, al día y completo.

Si una ruta cambia y nadie regenera el contrato, esta prueba falla con la instrucción para
regenerarlo: `python3 herramientas/exportar_openapi.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from callforge.presentation.api.app import create_app
from tests.conftest import make_test_settings

CONTRATO = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"
PUBLICAS = {("get", "/api/v1/health")}
ADMIN = {("post", "/api/v1/admin/tenants")}


@pytest.fixture(scope="module")
def esquema(tmp_path_factory):
    return create_app(make_test_settings(tmp_path_factory.mktemp("openapi"))).openapi()


def _operaciones() -> list[tuple[str, str, dict]]:
    publicado = json.loads(CONTRATO.read_text(encoding="utf-8"))
    return [(metodo, ruta, op) for ruta, ops in publicado["paths"].items() for metodo, op in ops.items()]


def test_el_contrato_versionado_esta_al_dia(esquema):
    publicado = json.dumps(esquema, indent=2, ensure_ascii=False) + "\n"
    assert CONTRATO.read_text(encoding="utf-8") == publicado, (
        "docs/api/openapi.json no coincide con la API: corre `python3 herramientas/exportar_openapi.py`")


def test_el_contrato_trae_todas_las_rutas_de_negocio(esquema):
    rutas = set(esquema["paths"])
    assert len(_operaciones()) == 18
    assert not any("companion" in r for r in rutas)                 # el modo retirado no se publica
    assert not any(r.startswith("/webchat") or r == "/dashboard" for r in rutas)  # páginas, no API


@pytest.mark.parametrize(("metodo", "ruta", "op"), _operaciones(),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_cada_operacion_esta_documentada(metodo: str, ruta: str, op: dict):
    assert op.get("summary", "").strip(), "falta summary"
    assert op.get("description", "").strip(), "falta description (docstring de la ruta)"
    exitos = {codigo: r for codigo, r in op["responses"].items() if codigo.startswith("2")}
    assert exitos, "sin respuesta de éxito documentada"
    for codigo, respuesta in op["responses"].items():
        assert respuesta.get("description", "").strip(), f"la respuesta {codigo} no tiene descripción"
        for tipo, contenido in (respuesta.get("content") or {}).items():
            assert contenido.get("schema"), f"la respuesta {codigo} ({tipo}) no declara esquema"
    if (metodo, ruta) in ADMIN:
        assert {"401", "403"} <= set(op["responses"]), "el alta de tenants documenta 401 y 403"
    elif (metodo, ruta) not in PUBLICAS:
        assert "401" in op["responses"], "una ruta protegida debe documentar el 401"
