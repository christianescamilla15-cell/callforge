from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from callforge.config import Settings
from callforge.presentation.api.app import create_app


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
