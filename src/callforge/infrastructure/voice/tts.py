"""Text-to-speech engines.

KokoroTTS runs fully on CPU (onnxruntime) on purpose: the 6GB GPU is
reserved for the LLM. Measured on the RTX 4050 laptop's Ryzen 7 7445HS:
warm RTF ~0.34 (a 3s sentence synthesizes in ~1.1s)."""
from __future__ import annotations

import io
import re
import threading
from abc import ABC, abstractmethod

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_MAX_CHUNK_CHARS = 280  # stays well under Kokoro's ~510-phoneme limit


def split_for_tts(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split text into sentence-ish chunks Kokoro can synthesize without
    truncating (it silently caps long inputs at its phoneme limit)."""
    chunks: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = sentence.rfind(",", 0, max_chars)
            if cut < 50:
                cut = sentence.rfind(" ", 0, max_chars)
            if cut < 50:
                cut = max_chars
            chunks.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            chunks.append(sentence)
    return chunks or [text.strip()]


class TTSEngine(ABC):
    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Return a WAV file (bytes) for the given text."""


def tts_cache_key(text: str, voice: str | None = None) -> str:
    """Cache identity of a phrase: voice + NORMALIZED text. Shared by the
    serving path (CachedTTS) and the voice-bank builder so pre-seeded
    cloned-voice entries match incoming requests exactly."""
    import hashlib

    from callforge.infrastructure.voice.normalize import normalize_for_tts

    normalized = normalize_for_tts(text)
    raw = f"{voice or ''}|{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


class CachedTTS(TTSEngine):
    """Disk-cached wrapper: normalizes the text (the speakable form IS the
    identity), then serves repeated phrases from cache/tts in ~0ms.

    The Fase-2 voice bank pre-seeds this same cache with cloned-voice WAVs,
    so fixed phrases come out in Christian's voice with zero extra code."""

    name = "cached-tts"

    def __init__(self, inner: TTSEngine, cache_dir: str = "cache/tts") -> None:
        from pathlib import Path

        self.inner = inner
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, text: str, voice: str | None = None) -> str:
        return tts_cache_key(text, voice)

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        from callforge.infrastructure.voice.normalize import normalize_for_tts

        path = self._dir / f"{self.cache_key(text, voice)}.wav"
        if path.exists():
            return path.read_bytes()
        wav = self.inner.synthesize(normalize_for_tts(text), voice)
        try:
            path.write_bytes(wav)
        except OSError:
            pass  # cache is best-effort
        return wav


class CartesiaTTS(TTSEngine):
    """Cloud TTS via Cartesia Sonic (cuasi-human quality, voice cloning).

    Spec pinned 2026-06-12 from docs.cartesia.ai: POST /tts/bytes with the
    Cartesia-Version date header. Uses ONE configured voice id; the cloud
    voice is consistent for fixed and dynamic speech alike. Synchronous
    httpx on purpose (callers already run us in a threadpool)."""

    name = "cartesia"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model: str = "sonic-3.5",
        language: str = "es",
        speed: float | None = None,  # 0.6-1.5; <1 = slower (calm/empathetic)
        base_url: str = "https://api.cartesia.ai",
        version: str = "2026-03-01",
        sample_rate: int = 44100,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("CartesiaTTS needs an API key")
        if not voice_id:
            raise ValueError("CartesiaTTS needs a voice_id")
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._language = language
        self._speed = speed
        self._base_url = base_url.rstrip("/")
        self._version = version
        self._sample_rate = sample_rate
        self._timeout = timeout

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        import httpx

        body = {
            "model_id": self._model,
            "transcript": text,
            "voice": {"mode": "id", "id": voice or self._voice_id},
            "language": self._language,
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": self._sample_rate,
            },
        }
        if self._speed is not None:
            body["generation_config"] = {"speed": self._speed}
        response = httpx.post(
            f"{self._base_url}/tts/bytes",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Cartesia-Version": self._version,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.content


class ClonedLiveTTS(TTSEngine):
    """Live cloned voice: synthesize with the inner engine (Kokoro, CPU)
    and re-timbre via the voice server's OpenVoice /convert (CPU, RTF ~0.33
    measured). If the converter is down, the plain inner audio ships - the
    reply NEVER fails because of the cloning layer."""

    name = "kokoro-cloned"

    def __init__(
        self,
        inner: TTSEngine,
        convert_url: str = "http://127.0.0.1:8002/convert",
        timeout: float = 60.0,
    ) -> None:
        self.inner = inner
        self._convert_url = convert_url
        self._timeout = timeout

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        import logging
        from urllib.parse import quote

        import httpx

        # `voice` selects the CLONE TARGET; the inner Kokoro keeps its
        # default voicepack (it is the prosody source, not the timbre).
        wav = self.inner.synthesize(text, None)
        url = self._convert_url
        if voice:
            url = f"{url}?voice={quote(voice)}"
        try:
            response = httpx.post(
                url,
                content=wav,
                headers={"Content-Type": "audio/wav"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.content
        except Exception as exc:  # noqa: BLE001 - cloning is best-effort
            logging.getLogger(__name__).warning(
                "Voice conversion failed (%s); serving plain TTS", exc
            )
            return wav


class ChatterboxRemoteTTS(TTSEngine):
    """Client for the Chatterbox voice-cloning micro-service (voice_server.py).

    Synchronous httpx on purpose: callers already run us in a threadpool."""

    name = "chatterbox"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8002",
        voice: str | None = None,
        language: str = "es",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.voice = voice
        self.language = language
        self._timeout = timeout

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        import httpx

        response = httpx.post(
            f"{self._base_url}/tts",
            json={
                "text": text,
                "language": self.language,
                "voice": voice or self.voice,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.content


class FallbackTTS(TTSEngine):
    """Same pattern as the LLM chain: try engines in order, first success wins."""

    name = "tts-fallback-chain"

    def __init__(self, engines: list[TTSEngine]) -> None:
        if not engines:
            raise ValueError("FallbackTTS needs at least one engine")
        self.engines = engines

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        import logging

        errors = []
        for engine in self.engines:
            try:
                return engine.synthesize(text, voice)
            except Exception as exc:  # noqa: BLE001 - degrade to next engine
                logging.getLogger(__name__).warning(
                    "TTS engine %s failed: %s", engine.name, exc
                )
                errors.append(f"{engine.name}: {exc}")
        raise RuntimeError("All TTS engines failed: " + "; ".join(errors))


class KokoroTTS(TTSEngine):
    name = "kokoro"

    def __init__(
        self,
        model_path: str,
        voices_path: str,
        voice: str = "ef_dora",
        lang: str = "es",
        speed: float = 1.0,
    ) -> None:
        self._model_path = model_path
        self._voices_path = voices_path
        self.voice = voice
        self.lang = lang
        self.speed = speed
        self._engine = None
        self._lock = threading.Lock()

    def _get_engine(self):
        # Lazy + locked: the ~340MB model loads once, on first use, and
        # onnxruntime sessions are not guaranteed re-entrant across loads.
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    from kokoro_onnx import Kokoro

                    self._engine = Kokoro(self._model_path, self._voices_path)
        return self._engine

    @staticmethod
    def _pause_after(chunk: str) -> float:
        # Sentence ends breathe longer than clause breaks.
        if chunk.endswith((".", "!", "?", "…")):
            return 0.25
        if chunk.endswith((",", ";", ":")):
            return 0.12
        return 0.15

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        import numpy as np
        import soundfile as sf

        engine = self._get_engine()
        pieces = []
        sample_rate = 24000
        with self._lock:
            for chunk in split_for_tts(text):
                samples, sample_rate = engine.create(
                    chunk, voice=voice or self.voice, speed=self.speed, lang=self.lang
                )
                pieces.append(samples)
                pieces.append(
                    np.zeros(
                        int(sample_rate * self._pause_after(chunk)),
                        dtype=samples.dtype,
                    )
                )
        audio = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        audio = self._polish(audio, sample_rate)
        buffer = io.BytesIO()
        sf.write(buffer, audio, sample_rate, format="WAV")
        return buffer.getvalue()

    @staticmethod
    def _polish(audio, sample_rate: int):
        """Click-free joins and consistent loudness: 4ms edge fades plus
        peak normalization to -1.5 dBFS. Deliberately light — heavier DSP
        only ships if it wins the listening A/B (see ROADMAP_VOZ Fase 4)."""
        import numpy as np

        fade = max(int(sample_rate * 0.004), 1)
        if audio.size > 2 * fade:
            audio = audio.copy()
            ramp = np.linspace(0.0, 1.0, fade, dtype=audio.dtype)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1e-6:
            audio = audio * (10 ** (-1.5 / 20) / peak)
        return audio
