import base64

import pytest
from fastapi.testclient import TestClient

from callforge.infrastructure.voice.stt import STTEngine
from callforge.infrastructure.voice.tts import TTSEngine
from callforge.presentation.api.app import create_app

from tests.conftest import make_test_settings

FAKE_WAV = b"RIFF....WAVEfake-audio-bytes"


class FakeTTS(TTSEngine):
    name = "fake-tts"

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        return FAKE_WAV


class FakeSTT(STTEngine):
    name = "fake-stt"

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def transcribe(self, audio: bytes, filename: str, language: str = "es") -> str:
        return self.transcript


def test_split_for_tts_respects_chunk_limit():
    from callforge.infrastructure.voice.tts import split_for_tts

    short = split_for_tts("Hola. ¿Cómo estás?")
    assert short == ["Hola.", "¿Cómo estás?"]

    long_sentence = "palabra " * 100  # 800 chars, no sentence breaks
    chunks = split_for_tts(long_sentence)
    assert len(chunks) > 1
    assert all(len(c) <= 280 for c in chunks)
    assert " ".join(chunks).split() == long_sentence.split()  # no text lost


class BrokenTTS(TTSEngine):
    name = "broken-tts"

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        raise ConnectionError("voice server down")


def test_cartesia_tts_builds_correct_request(monkeypatch):
    import httpx

    from callforge.infrastructure.voice.tts import CartesiaTTS

    captured = {}

    class FakeResponse:
        content = b"RIFF-cartesia-wav"

        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    engine = CartesiaTTS(api_key="sk_car_test", voice_id="voice-es-1", language="es")
    out = engine.synthesize("Hola mundo")

    assert out == b"RIFF-cartesia-wav"
    assert captured["url"] == "https://api.cartesia.ai/tts/bytes"
    assert captured["headers"]["Authorization"] == "Bearer sk_car_test"
    assert captured["headers"]["Cartesia-Version"] == "2026-03-01"
    body = captured["json"]
    assert body["model_id"] == "sonic-3.5"
    assert body["transcript"] == "Hola mundo"
    assert body["voice"] == {"mode": "id", "id": "voice-es-1"}
    assert body["language"] == "es"
    assert body["output_format"]["container"] == "wav"


def test_cartesia_requires_key_and_voice():
    from callforge.infrastructure.voice.tts import CartesiaTTS

    with pytest.raises(ValueError):
        CartesiaTTS(api_key="", voice_id="v")
    with pytest.raises(ValueError):
        CartesiaTTS(api_key="k", voice_id="")


def test_cartesia_falls_back_to_local_when_cloud_down(monkeypatch):
    import httpx

    from callforge.infrastructure.voice.tts import CartesiaTTS, FallbackTTS

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("no internet")

    monkeypatch.setattr(httpx, "post", fake_post)

    cartesia = CartesiaTTS(api_key="k", voice_id="v")
    chain = FallbackTTS([cartesia, FakeTTS()])
    assert chain.synthesize("hola") == FAKE_WAV  # local safety net served


def test_cached_tts_serves_from_disk_and_normalizes(tmp_path):
    from callforge.infrastructure.voice.tts import CachedTTS, TTSEngine

    class CountingTTS(TTSEngine):
        name = "counting"

        def __init__(self):
            self.calls = []

        def synthesize(self, text, voice=None):
            self.calls.append(text)
            return FAKE_WAV

    inner = CountingTTS()
    cached = CachedTTS(inner, cache_dir=str(tmp_path / "tts"))

    assert cached.synthesize("Paga antes del dia 10") == FAKE_WAV
    assert cached.synthesize("Paga antes del dia 10") == FAKE_WAV  # cache hit
    assert len(inner.calls) == 1
    assert "diez" in inner.calls[0]  # normalized before reaching the engine

    # different voice -> different cache entry
    cached.synthesize("Paga antes del dia 10", voice="otra")
    assert len(inner.calls) == 2


def test_cloned_live_tts_degrades_to_inner_when_converter_down():
    from callforge.infrastructure.voice.tts import ClonedLiveTTS

    # Converter URL points nowhere -> the inner engine's audio must ship
    engine = ClonedLiveTTS(FakeTTS(), convert_url="http://127.0.0.1:1/convert")
    assert engine.synthesize("hola") == FAKE_WAV


def test_fallback_tts_degrades_to_next_engine():
    from callforge.infrastructure.voice.tts import FallbackTTS

    chain = FallbackTTS([BrokenTTS(), FakeTTS()])
    assert chain.synthesize("hola") == FAKE_WAV


def test_fallback_tts_raises_when_all_fail():
    from callforge.infrastructure.voice.tts import FallbackTTS

    chain = FallbackTTS([BrokenTTS(), BrokenTTS()])
    with pytest.raises(RuntimeError):
        chain.synthesize("hola")


@pytest.fixture
def voice_client(tmp_path):
    app = create_app(make_test_settings(tmp_path))
    app.state.tts = FakeTTS()
    app.state.stt = FakeSTT("Mi internet no funciona desde ayer")
    with TestClient(app) as client:
        yield client


def test_tts_endpoint_returns_wav(voice_client):
    response = voice_client.post("/api/v1/voice/tts", json={"text": "Hola, te ayudo"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == FAKE_WAV


def test_fillers_endpoint_returns_texts(voice_client):
    fillers = voice_client.get("/api/v1/voice/fillers").json()
    assert isinstance(fillers, list) and len(fillers) >= 2
    assert all(isinstance(f, str) and f for f in fillers)


def test_kokoro_polish_normalizes_and_fades():
    import numpy as np

    from callforge.infrastructure.voice.tts import KokoroTTS

    sr = 24000
    audio = np.ones(sr, dtype=np.float32) * 0.2  # 1s constant signal
    polished = KokoroTTS._polish(audio, sr)
    assert polished[0] == 0.0  # fade-in starts at silence
    assert abs(polished[-1]) < 1e-6  # fade-out ends at silence
    peak = float(np.max(np.abs(polished)))
    assert 0.82 < peak < 0.85  # -1.5 dBFS ~= 0.841


def test_tts_disabled_returns_503(client):
    response = client.post("/api/v1/voice/tts", json={"text": "hola"})
    assert response.status_code == 503


def test_voice_message_full_turn(voice_client):
    conversation_id = voice_client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    response = voice_client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message",
        files={"file": ("voice.webm", b"fake-webm-bytes", "audio/webm")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "Mi internet no funciona desde ayer"
    assert body["agent_used"] == "troubleshooting"  # STT text routed normally
    assert body["reply"]
    assert base64.b64decode(body["audio_b64"]) == FAKE_WAV

    # The transcribed text is persisted as the customer message
    detail = voice_client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["messages"][0]["content"] == "Mi internet no funciona desde ayer"


def test_voice_message_stream_mode_returns_sentence_pipeline(voice_client):
    conversation_id = voice_client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    body = voice_client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message?stream=true",
        files={"file": ("voice.webm", b"fake-webm-bytes", "audio/webm")},
    ).json()
    # Mock troubleshooting reply has 3 sentences -> first spoken, 2 remaining
    assert body["audio_b64"]
    assert len(body["remaining_sentences"]) >= 1
    joined = " ".join([body["remaining_sentences"][0]])
    assert joined  # plain text chunks, client fetches them via /voice/tts


def test_voice_message_default_returns_full_audio(voice_client):
    conversation_id = voice_client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    body = voice_client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message",
        files={"file": ("voice.webm", b"fake-webm-bytes", "audio/webm")},
    ).json()
    assert body["audio_b64"]
    assert body["remaining_sentences"] == []


def test_upload_voice_wav_and_list(voice_client, tmp_path, monkeypatch):
    # Isolate the voices dir, and force the local-listing fallback by pointing
    # the voice-server proxy at a dead port (otherwise a live :8002 answers).
    voice_client.app.state.settings.voices_dir = str(tmp_path / "voices")
    voice_client.app.state.settings.voice_server_url = "http://127.0.0.1:1"
    wav_bytes = b"RIFF" + b"\\x00" * 60_000
    response = voice_client.post(
        "/api/v1/voice/voices?name=cliente-vip",
        files={"file": ("ref.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "cliente-vip"
    assert (tmp_path / "voices" / "cliente-vip.wav").exists()

    listed = voice_client.get("/api/v1/voice/voices").json()
    assert listed["engine"] == "local"
    assert any(v["id"] == "cliente-vip" for v in listed["voices"])  # {id,name} shape


def test_voices_endpoint_lists_cartesia_when_engine_cartesia(voice_client):
    # Pre-seed the cached voice list so the endpoint needs no network.
    voice_client.app.state.settings.tts_engine = "cartesia"
    voice_client.app.state.settings.cartesia_api_key = "sk_car_test"
    voice_client.app.state.settings.cartesia_voice_id = "voice-nuria"
    voice_client.app.state.cartesia_voices = [
        {"id": "voice-nuria", "name": "Nuria - Trusted Advisor"},
        {"id": "voice-pedro", "name": "Pedro - Formal Speaker"},
    ]
    data = voice_client.get("/api/v1/voice/voices").json()
    assert data["engine"] == "cartesia"
    assert data["default"] == "voice-nuria"
    assert {v["id"] for v in data["voices"]} == {"voice-nuria", "voice-pedro"}


def test_upload_voice_rejects_bad_name_and_short_audio(voice_client, tmp_path):
    voice_client.app.state.settings.voices_dir = str(tmp_path / "voices")
    assert voice_client.post(
        "/api/v1/voice/voices?name=../evil",
        files={"file": ("ref.wav", b"RIFF" + b"\\x00" * 60_000, "audio/wav")},
    ).status_code == 422
    assert voice_client.post(
        "/api/v1/voice/voices?name=corta",
        files={"file": ("ref.wav", b"tiny", "audio/wav")},
    ).status_code == 422


def test_voice_message_persists_audio_to_inbox(voice_client, tmp_path):
    conversation_id = voice_client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    voice_client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message",
        files={"file": ("voice.webm", b"fake-webm-bytes", "audio/webm")},
    )
    inbox = tmp_path / "voice_inbox"
    files = list(inbox.glob("*.webm"))
    assert len(files) == 1
    assert files[0].read_bytes() == b"fake-webm-bytes"


def test_voice_message_empty_audio_rejected(voice_client):
    conversation_id = voice_client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    response = voice_client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message",
        files={"file": ("voice.webm", b"", "audio/webm")},
    )
    assert response.status_code == 422


def test_voice_message_without_stt_returns_503(client):
    conversation_id = client.post("/api/v1/conversations/start", json={}).json()[
        "conversation_id"
    ]
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/voice-message",
        files={"file": ("voice.webm", b"fake", "audio/webm")},
    )
    assert response.status_code == 503
