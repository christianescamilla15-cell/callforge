"""Speech-to-text via Groq Whisper (whisper-large-v3-turbo).

Measured live 2026-06-10: 8s of Spanish audio transcribed verbatim in 0.9s.
Pricing $0.04/hour of audio; Groq bills a 10s minimum per request."""
from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class STTEngine(ABC):
    name: str = "base"

    @abstractmethod
    async def transcribe(self, audio: bytes, filename: str, language: str = "es") -> str: ...


class GroqSTT(STTEngine):
    name = "groq-whisper"

    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def transcribe(self, audio: bytes, filename: str, language: str = "es") -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio)},
                data={"model": self._model, "language": language},
            )
            response.raise_for_status()
        return (response.json().get("text") or "").strip()
