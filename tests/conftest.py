from __future__ import annotations

import os

# Importar callforge.presentation.api.app crea la app del módulo (la de uvicorn) con la configuración
# del entorno: en las pruebas, que sea en memoria y sin proveedores externos (sin crear
# ./support_agents.db en el repo). Cada prueba arma su propia app con make_test_settings.
for _clave, _valor in {"DATABASE_URL": "sqlite://", "OLLAMA_ENABLED": "false", "TTS_ENABLED": "false",
                       "GROQ_API_KEY": "", "MOCK_ENABLED": "true"}.items():
    os.environ.setdefault(_clave, _valor)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from callforge.config import Settings  # noqa: E402
from callforge.presentation.api.app import create_app  # noqa: E402


def make_test_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        groq_api_key="",
        ollama_enabled=False,
        mock_enabled=True,
        api_token="",
        tts_enabled=False,
        voice_inbox_dir=str(tmp_path / "voice_inbox"),
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_test_settings(tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
