"""Paridad contra un oráculo independiente (N08b): el lote golden de conversaciones de punta a punta.

El lote (`golden/conversaciones/`) son 7 conversaciones y 12 turnos de soporte. El resultado
esperado lo escribe `golden/generar_golden.py`, que NO importa callforge: deriva cada turno de las
reglas de docs/reglas.md. La prueba hace lo que haría la operación real —abre cada conversación y
manda sus mensajes por la API— y compara turno por turno, por MULTICONJUNTO con llave
(conversación, turno), más las cifras control del lote (conteos de corridas, eventos, mensajes,
tickets y pasos, y el hash del lote) y el consumo (tokens y costo en Decimal).

El listón es el CONTROL NEGATIVO: un esperado alterado y una entrada alterada deben producir
exactamente las diferencias sembradas en `control_negativo/alteraciones.json`. Si el comparador
dijera «todo igual» siempre, estas pruebas fallarían.

Todo es determinista y sin red: el LLM es el MockProvider, tarifado como el modelo de Groq por
omisión para que el costo no sea trivialmente cero.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import callforge.presentation.api.app as modulo_app
from callforge.infrastructure.llm.base import LLMResult, estimate_cost
from callforge.infrastructure.llm.fallback import FallbackLLMProvider
from callforge.infrastructure.llm.mock_provider import MockProvider
from tests.conftest import make_test_settings
from tests.paridad import COLUMNAS, comparar, hash_lote, inversiones, leer_csv, normalizar

GOLDEN = Path(__file__).resolve().parent / "golden"
LOTE = GOLDEN / "conversaciones"
CONTROL_NEGATIVO = LOTE / "control_negativo"
CONSUMO = LOTE / "consumo_aprobado.json"

MODELO_TARIFADO = "llama-3.1-8b-instant"
# Precio publicado por Groq para ese modelo, USD por millón de tokens (copia independiente de la tabla
# de infrastructure/llm/base.py: si una cambia sin la otra, la prueba de costo lo dice).
PRECIO_POR_MILLON = {"entrada": Decimal("0.05"), "salida": Decimal("0.08")}


class MockTarifado(MockProvider):
    """El MockProvider determinista, cobrando como el modelo de Groq por omisión."""

    name = "mock-tarifado"

    async def complete(self, system, messages, json_mode=False) -> LLMResult:
        resultado = await super().complete(system, messages, json_mode=json_mode)
        resultado.provider, resultado.model = self.name, MODELO_TARIFADO
        resultado.estimated_cost = estimate_cost(MODELO_TARIFADO, resultado.tokens_in, resultado.tokens_out)
        return resultado


def _leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def _entrada(ruta: Path = LOTE / "entrada.json") -> list[dict]:
    return json.loads(_leer(ruta))["conversaciones"]


@pytest.fixture
def sistema(tmp_path, monkeypatch):
    monkeypatch.setattr(modulo_app, "build_provider_chain", lambda _s: FallbackLLMProvider([MockTarifado()]))
    with TestClient(modulo_app.create_app(make_test_settings(tmp_path))) as cliente:
        yield cliente


def _sql(cliente, consulta: str, **parametros) -> list[tuple]:
    with cliente.app.state.engine.connect() as conexion:
        return [tuple(f) for f in conexion.execute(text(consulta), parametros)]


def _corridas(cliente, cid: str) -> list[str]:
    # rowid: orden de inserción (dos corridas pueden compartir el mismo microsegundo).
    return [f[0] for f in _sql(cliente, "SELECT agent_name FROM agent_runs WHERE conversation_id = :c "
                                        "ORDER BY rowid", c=cid)]


def correr_lote(cliente, conversaciones: list[dict]) -> tuple[list[tuple[str, ...]], dict[str, str]]:
    """Opera el lote por la API y devuelve (filas obtenidas, id de conversación por clave)."""
    filas, ids = [], {}
    for conversacion in conversaciones:
        cid = cliente.post("/api/v1/conversations/start", json={
            "customer_external_id": conversacion["cliente"], "channel": conversacion["canal"],
        }).json()["conversation_id"]
        ids[conversacion["clave"]] = cid
        pasos_previos = 0
        for turno, mensaje in enumerate(conversacion["mensajes"], start=1):
            corridas_previas = len(_corridas(cliente, cid))
            respuesta = cliente.post(f"/api/v1/conversations/{cid}/message", json={"content": mensaje})
            assert respuesta.status_code == 200, respuesta.text
            cuerpo = respuesta.json()
            detalle = cliente.get(f"/api/v1/conversations/{cid}").json()
            pasos = detalle["resolution_steps"]
            ticket = (cliente.get(f"/api/v1/tickets/{cuerpo['ticket_id']}").json()
                      if cuerpo["ticket_id"] else {})
            filas.append(normalizar({
                "conversacion": conversacion["clave"], "turno": str(turno),
                "agente": cuerpo["agent_used"],
                "ruta": ">".join(_corridas(cliente, cid)[corridas_previas:]),
                "intent": cuerpo["intent"], "categoria": cuerpo["category"], "urgencia": cuerpo["urgency"],
                "escalada": "si" if cuerpo["escalated"] else "no",
                "ticket_prioridad": ticket.get("priority", ""), "ticket_titulo": ticket.get("title", ""),
                "estado": cuerpo["conversation_status"],
                "paso": str(pasos[-1]["step_number"]) if len(pasos) > pasos_previos else "",
                "con_resumen": "si" if detalle["summary"] else "no",
            }))
            pasos_previos = len(pasos)
    return filas, ids


ESPERADO = leer_csv(_leer(LOTE / "esperado.csv"))


# --------------------------------------------------------------------------- #
# El oráculo es coherente consigo mismo
# --------------------------------------------------------------------------- #


def test_el_golden_esta_al_dia_con_su_oraculo():
    """Nadie editó a mano un archivo del lote: regenerarlo desde el oráculo da lo mismo, byte a byte."""
    spec = importlib.util.spec_from_file_location("generar_golden", GOLDEN / "generar_golden.py")
    oraculo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = oraculo
    spec.loader.exec_module(oraculo)
    archivos = oraculo.construir()
    assert sorted(archivos) == sorted([
        "esperado.csv", "cifras_control.json", "control_negativo/esperado_alterado.csv",
        "control_negativo/entrada_alterada.json", "control_negativo/alteraciones.json"])
    for nombre, contenido in archivos.items():
        assert _leer(LOTE / nombre) == contenido, f"{nombre} no coincide con generar_golden.py"
    assert tuple(oraculo.COLUMNAS) == COLUMNAS


def test_el_hash_del_lote_coincide_entre_oraculo_y_comparador():
    cifras = json.loads(_leer(LOTE / "cifras_control.json"))
    assert hash_lote(ESPERADO) == cifras["hash_lote"]
    assert len(ESPERADO) == cifras["turnos"]


# --------------------------------------------------------------------------- #
# Paridad del sistema contra el golden
# --------------------------------------------------------------------------- #


def test_paridad_del_lote_contra_el_golden(sistema):
    obtenido, _ = correr_lote(sistema, _entrada())
    diferencias = comparar(ESPERADO, obtenido)
    assert diferencias.como_dict() == {"faltantes": [], "sobrantes": [], "distintas": {}}
    assert hash_lote(obtenido) == json.loads(_leer(LOTE / "cifras_control.json"))["hash_lote"]


def test_cifras_control_del_lote(sistema):
    """Lo persistido (corridas, eventos, mensajes, tickets, pasos, estados) cuadra con el oráculo."""
    correr_lote(sistema, _entrada())
    cifras = json.loads(_leer(LOTE / "cifras_control.json"))
    metricas = sistema.get("/api/v1/metrics").json()
    obtenidas = {
        "conversaciones": metricas["conversations"]["total"],
        "mensajes": metricas["messages_total"],
        "corridas_total": metricas["agent_runs_total"],
        "corridas_por_agente": dict(_sql(sistema, "SELECT agent_name, count(*) FROM agent_runs "
                                                  "GROUP BY agent_name ORDER BY agent_name")),
        "eventos_por_tipo": dict(_sql(sistema, "SELECT event_type, count(*) FROM agent_events "
                                               "GROUP BY event_type ORDER BY event_type")),
        "tickets_por_prioridad": dict(Counter(t["priority"] for t in sistema.get("/api/v1/tickets").json())),
        "estado_final": dict(sorted(metricas["conversations"]["by_status"].items())),
        "pasos": dict(_sql(sistema, "SELECT status, count(*) FROM resolution_steps GROUP BY status")),
    }
    obtenidas["pasos"] = {"total": sum(obtenidas["pasos"].values()), "answered": obtenidas["pasos"].get(
        "answered", 0), "proposed": obtenidas["pasos"].get("proposed", 0)}
    esperadas = {k: cifras[k] for k in obtenidas}
    assert obtenidas == esperadas
    assert metricas["agent_errors_total"] == 0


def test_consumo_del_lote_en_decimal(sistema):
    """Tokens aprobados y costo: el oráculo calcula el costo en Decimal con el precio publicado y debe
    coincidir con lo que guardó cada corrida y con lo que reporta /metrics (redondeado a 6)."""
    correr_lote(sistema, _entrada())
    (uso,) = sistema.get("/api/v1/metrics").json()["llm_usage"]
    observado = {"proveedor": uso["provider"], "modelo": MODELO_TARIFADO, "llamadas": uso["calls"],
                 "tokens_in": uso["tokens_in"], "tokens_out": uso["tokens_out"]}
    if os.environ.get("CALLFORGE_APROBAR_CONSUMO") == "1":   # cambio consciente de prompts: re-aprobar
        CONSUMO.write_text(json.dumps(observado, indent=2) + "\n", encoding="utf-8")
    aprobado = json.loads(_leer(CONSUMO))
    assert observado == aprobado, (
        "el consumo cambió (¿cambió el texto de un prompt?); revisa y re-aprueba con "
        "CALLFORGE_APROBAR_CONSUMO=1 pytest tests/test_paridad_golden.py -k consumo")

    costo_oraculo = (Decimal(aprobado["tokens_in"]) * PRECIO_POR_MILLON["entrada"]
                     + Decimal(aprobado["tokens_out"]) * PRECIO_POR_MILLON["salida"]) / Decimal(1_000_000)
    costo_corridas = sum((Decimal(str(c)) for (c,) in _sql(sistema, "SELECT estimated_cost FROM agent_runs")),
                         Decimal(0))
    assert abs(costo_corridas - costo_oraculo) < Decimal("1e-12")
    assert Decimal(str(uso["estimated_cost_usd"])) == costo_oraculo.quantize(Decimal("0.000001"))
    assert costo_oraculo > 0
    assert aprobado["llamadas"] == json.loads(_leer(LOTE / "cifras_control.json"))["corridas_total"]


def test_el_orden_de_operacion_no_cambia_el_resultado_y_los_turnos_quedan_en_orden(sistema):
    """Comparación por multiconjunto: operar el lote al revés da las mismas filas. El orden que sí
    importa —los mensajes de cada conversación— se mide en inversiones: 0."""
    conversaciones = list(reversed(_entrada()))
    obtenido, ids = correr_lote(sistema, conversaciones)
    assert inversiones([f[0] for f in obtenido]) > 0            # se exportó en otro orden…
    assert comparar(ESPERADO, obtenido).vacia                   # …y aun así es el mismo lote
    for conversacion in conversaciones:
        leidos = [m["content"] for m in sistema.get(f"/api/v1/conversations/{ids[conversacion['clave']]}")
                  .json()["messages"] if m["role"] == "customer"]
        posicion = {mensaje: i for i, mensaje in enumerate(conversacion["mensajes"])}
        assert inversiones([posicion[m] for m in leidos]) == 0


# --------------------------------------------------------------------------- #
# Control negativo: el comparador DEBE ver las diferencias sembradas
# --------------------------------------------------------------------------- #


ALTERACIONES = json.loads(_leer(CONTROL_NEGATIVO / "alteraciones.json"))


def test_control_negativo_un_esperado_alterado_da_exactamente_las_diferencias_sembradas(sistema):
    obtenido, _ = correr_lote(sistema, _entrada())
    alterado = leer_csv(_leer(CONTROL_NEGATIVO / "esperado_alterado.csv"))
    assert comparar(alterado, obtenido).como_dict() == ALTERACIONES["esperado_alterado"]


def test_control_negativo_una_entrada_alterada_cambia_solo_su_conversacion(sistema):
    obtenido, _ = correr_lote(sistema, _entrada(CONTROL_NEGATIVO / "entrada_alterada.json"))
    diferencias = comparar(ESPERADO, obtenido).como_dict()
    assert diferencias == ALTERACIONES["entrada_alterada"]
    assert diferencias["distintas"]                              # sí hubo diferencias que encontrar


def test_control_negativo_las_inversiones_cuentan_desplazamientos_no_posiciones():
    assert inversiones([0, 1, 2, 3, 4]) == 0
    assert inversiones([1, 2, 3, 4, 0]) == 4      # una fila desplazada 4 lugares: 4, no «5 de 5 mal»
    assert inversiones([4, 3, 2, 1, 0]) == 10
