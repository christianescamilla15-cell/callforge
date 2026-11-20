"""E2E: el mismo recorrido contra la app en proceso (por omisión) o contra un servidor vivo.

Sin variables, la app se arma en proceso con TestClient (HTTP ASGI real, SQLite temporal, sin red).
Con CALLFORGE_E2E_URL (p. ej. la imagen de producción arrancada por el CI), las mismas pruebas van
por HTTP y WebSocket de verdad; el servidor debe tener ADMIN_TOKEN y API_TOKEN iguales a
CALLFORGE_E2E_ADMIN_TOKEN y CALLFORGE_E2E_API_TOKEN, y el LLM en modo sin red (MOCK_ENABLED).
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from callforge.presentation.api.app import create_app
from tests.conftest import make_test_settings

URL_VIVA = os.environ.get("CALLFORGE_E2E_URL", "").rstrip("/")

# Cómo se ve un socket rechazado: en proceso, el cierre 4401; contra el servidor, el handshake negado.
RECHAZOS_WEBCHAT = (WebSocketDisconnect, InvalidHandshake, ConnectionClosed)


@dataclass
class Sistema:
    http: httpx.Client | TestClient
    admin_token: str
    api_token: str

    def webchat(self, consulta: str):
        """Socket del webchat con la misma interfaz en proceso y contra el servidor vivo."""
        if isinstance(self.http, TestClient):
            return self.http.websocket_connect(f"/webchat/ws?{consulta}")
        from websockets.sync.client import connect

        return _SocketVivo(connect(f"{URL_VIVA.replace('http', 'ws', 1)}/webchat/ws?{consulta}"))


class _SocketVivo:
    def __init__(self, conexion) -> None:
        self._c = conexion

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self._c.close()

    def send_text(self, texto: str) -> None:
        self._c.send(texto)

    def receive_json(self) -> dict:
        return json.loads(self._c.recv(timeout=30))


@pytest.fixture
def sistema(tmp_path):
    if URL_VIVA:
        with httpx.Client(base_url=URL_VIVA, timeout=30) as http:
            yield Sistema(http, admin_token=os.environ["CALLFORGE_E2E_ADMIN_TOKEN"],
                          api_token=os.environ["CALLFORGE_E2E_API_TOKEN"])
        return
    settings = make_test_settings(tmp_path)
    settings.admin_token = secrets.token_urlsafe(16)
    settings.api_token = secrets.token_urlsafe(16)
    with TestClient(create_app(settings)) as http:
        yield Sistema(http, settings.admin_token, settings.api_token)
