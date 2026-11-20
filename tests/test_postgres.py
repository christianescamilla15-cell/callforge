"""Integración contra PostgreSQL real (N03): migraciones y un caso completo sobre el motor declarado.

docker-compose ofrece PostgreSQL 16 (perfil «postgres») además del SQLite por omisión. Estas pruebas
corren cuando CALLFORGE_TEST_POSTGRES_URL apunta a una base vacía (el CI levanta postgres:16 como
servicio). Con CALLFORGE_EXIGIR_POSTGRES=1 (lo pone el CI) la falta de la URL es un FALLO, no un
salto silencioso; en local, sin Postgres, se saltan con ese motivo explícito.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

import callforge.migrations as paquete_migraciones
from callforge.infrastructure.database import build_engine, run_migrations
from callforge.presentation.api.app import create_app
from tests.conftest import make_test_settings

URL = os.environ.get("CALLFORGE_TEST_POSTGRES_URL", "")
if not URL and os.environ.get("CALLFORGE_EXIGIR_POSTGRES") == "1":
    raise RuntimeError("CALLFORGE_EXIGIR_POSTGRES=1 pero falta CALLFORGE_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="sin CALLFORGE_TEST_POSTGRES_URL: integración con PostgreSQL sólo en el CI o a mano")


@pytest.fixture
def motor():
    engine = build_engine(URL)
    with engine.begin() as conexion:   # base limpia: cada prueba parte de un esquema vacío
        conexion.execute(text("DROP SCHEMA public CASCADE"))
        conexion.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def _config(conexion) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(paquete_migraciones.__file__).resolve().parent))
    config.attributes["connection"] = conexion
    return config


def test_postgres_migra_a_head_y_la_0006_va_y_vuelve(motor):
    run_migrations(motor)
    tablas = set(inspect(motor).get_table_names())
    assert "companion_memories" not in tablas
    assert {"conversations", "tickets", "tenants", "resolution_steps"} <= tablas
    with motor.connect() as conexion:
        command.downgrade(_config(conexion), "0005")
        conexion.commit()
    assert "companion_memories" in inspect(motor).get_table_names()
    with motor.connect() as conexion:
        command.upgrade(_config(conexion), "head")
        conexion.commit()
        assert conexion.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0006"
    assert "companion_memories" not in inspect(motor).get_table_names()


def test_postgres_caso_completo_y_aislamiento_por_tenant(motor, tmp_path):
    settings = make_test_settings(tmp_path)
    settings.database_url = URL
    settings.admin_token = "admin-de-integracion"
    with TestClient(create_app(settings)) as cliente:
        admin = {"X-Admin-Token": settings.admin_token}
        a = {"X-API-Token": cliente.post("/api/v1/admin/tenants", json={"name": "A"}, headers=admin).json()[
            "api_key"]}
        b = {"X-API-Token": cliente.post("/api/v1/admin/tenants", json={"name": "B"}, headers=admin).json()[
            "api_key"]}
        cid = cliente.post("/api/v1/conversations/start", json={}, headers=a).json()["conversation_id"]
        tecnico = cliente.post(f"/api/v1/conversations/{cid}/message",
                               json={"content": "Mi internet no funciona"}, headers=a).json()
        escalado = cliente.post(f"/api/v1/conversations/{cid}/message",
                                json={"content": "Quiero hablar con un humano"}, headers=a).json()
        assert tecnico["agent_used"] == "troubleshooting"
        assert escalado["escalated"] is True and escalado["ticket_id"]
        assert cliente.get(f"/api/v1/conversations/{cid}", headers=b).status_code == 404
        metricas_a = cliente.get("/api/v1/metrics", headers=a).json()
        assert metricas_a["conversations"]["by_status"] == {"escalated": 1}
        assert metricas_a["tickets_total"] == 1 and metricas_a["agent_runs_total"] == 6
        assert cliente.get("/api/v1/metrics", headers=b).json()["conversations"]["total"] == 0
