"""Chatterbox voice-cloning TTS micro-service.

Runs in its OWN venv (.venv-voice, torch cu124) on :8002, separate from the
CallForge venv. CallForge talks to it via ChatterboxRemoteTTS and falls back
to local Kokoro when this service is down or slow.

Run:  .venv-voice\\Scripts\\python.exe -m uvicorn voice_server:app --port 8002

Voice references: drop 5-15s WAV/MP3 clips into voices/ ; request them by
filename stem (e.g. voices/christian.wav -> "voice": "christian").
"""
from __future__ import annotations

import io
import os
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

DEVICE = os.environ.get("CHATTERBOX_DEVICE", "cuda")
VOICES_DIR = Path(os.environ.get("CHATTERBOX_VOICES_DIR", "voices"))
# OpenVoice tone-color conversion runs on CPU on purpose (measured RTF
# 0.30-0.37 on this machine) - the GPU stays free for the LLM / watcher.
VC_DEVICE = os.environ.get("OPENVOICE_DEVICE", "cpu")
VC_DEFAULT_VOICE = os.environ.get("OPENVOICE_DEFAULT_VOICE", "christian_full")
VC_SOURCE_SAMPLE = os.environ.get(
    "OPENVOICE_SOURCE_SAMPLE", "scripts/voice_ab/ef_dora_x10.wav"
)

app = FastAPI(title="CallForge Voice Server (Chatterbox + OpenVoice)")

_model = None
_converter = None
_src_se = None
_target_se_cache: dict = {}  # voice name -> speaker embedding (zero-shot)
_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from chatterbox.mtl_tts import ChatterboxMultilingualTTS

                _model = ChatterboxMultilingualTTS.from_pretrained(device=DEVICE)
    return _model


def get_converter():
    """OpenVoice V2 tone-color converter + cached SOURCE embedding."""
    global _converter, _src_se
    if _converter is None:
        with _lock:
            if _converter is None:
                from huggingface_hub import snapshot_download
                from openvoice.api import ToneColorConverter

                ckpt = Path(
                    snapshot_download(
                        "myshell-ai/OpenVoiceV2", allow_patterns=["converter/*"]
                    )
                ) / "converter"
                converter = ToneColorConverter(str(ckpt / "config.json"), device=VC_DEVICE)
                converter.load_ckpt(str(ckpt / "checkpoint.pth"))
                _src_se = converter.extract_se([VC_SOURCE_SAMPLE])
                _converter = converter
    return _converter, _src_se


def get_target_se(converter, voice: str):
    """Zero-shot: a voice is just an embedding extracted once from its
    reference file in voices/. Cached in memory AND on disk (.se_cache/) so
    switching voices is instant even after a service restart - extraction is
    the slow part, and now it happens only once per voice, ever."""
    if voice in _target_se_cache:
        return _target_se_cache[voice]
    import torch

    cache_dir = VOICES_DIR / ".se_cache"
    cache_file = cache_dir / f"{voice}.pt"
    if cache_file.exists():
        try:
            se = torch.load(str(cache_file), map_location=VC_DEVICE)
            _target_se_cache[voice] = se
            return se
        except Exception:  # noqa: BLE001 - stale/corrupt cache -> re-extract
            pass
    ref = _resolve_voice(voice)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Voice '{voice}' not found")
    with _lock:
        se = converter.extract_se([ref])
    _target_se_cache[voice] = se
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        torch.save(se, str(cache_file))
    except Exception:  # noqa: BLE001 - persistence is best-effort
        pass
    return se


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    language: str = "es"
    voice: str | None = None  # filename stem inside voices/


def _resolve_voice(voice: str | None) -> str | None:
    if not voice:
        return None
    safe = Path(voice).name  # no path traversal
    for ext in (".wav", ".mp3", ".flac"):
        candidate = VOICES_DIR / f"{safe}{ext}"
        if candidate.exists():
            return str(candidate)
    return None


def _list_voices() -> list[str]:
    if not VOICES_DIR.exists():
        return []
    return sorted(
        p.stem for p in VOICES_DIR.glob("*") if p.suffix in (".wav", ".mp3", ".flac")
    )


@app.get("/voices")
def list_voices() -> dict:
    return {"default": VC_DEFAULT_VOICE, "voices": _list_voices()}


@app.post("/warm")
def warm(voice: str | None = None) -> dict:
    """Pre-extract (and disk-cache) a voice's embedding so its FIRST use is
    instant. Call right after cloning a new voice; switching to it live then
    has zero extraction lag."""
    converter, _ = get_converter()
    target = voice or VC_DEFAULT_VOICE
    get_target_se(converter, target)
    return {"warmed": target, "cached": sorted(_target_se_cache)}


@app.post("/convert")
def convert(
    wav: bytes = Body(..., media_type="audio/wav"), voice: str | None = None
) -> Response:
    """Re-timbre a WAV body (e.g. Kokoro output) into a cloned voice.

    ?voice=<name> picks any reference in voices/ (zero-shot, embedding
    cached on first use); default is OPENVOICE_DEFAULT_VOICE.

    OpenVoice's API is path-based, so the audio round-trips through temp
    files; negligible next to the conversion itself (CPU RTF ~0.33)."""
    import tempfile
    import time

    converter, src_se = get_converter()
    tgt_se = get_target_se(converter, voice or VC_DEFAULT_VOICE)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / "in.wav"
        out_path = Path(tmp) / "out.wav"
        src_path.write_bytes(wav)
        with _lock:
            converter.convert(
                audio_src_path=str(src_path),
                src_se=src_se,
                tgt_se=tgt_se,
                output_path=str(out_path),
                message="@CallForge",
            )
        converted = out_path.read_bytes()
    return Response(
        content=converted,
        media_type="audio/wav",
        headers={"X-Convert-Seconds": f"{time.perf_counter() - started:.2f}"},
    )


@app.get("/health")
def health() -> dict:
    voices = (
        sorted(p.stem for p in VOICES_DIR.glob("*") if p.suffix in (".wav", ".mp3", ".flac"))
        if VOICES_DIR.exists()
        else []
    )
    return {
        "status": "ok",
        "device": DEVICE,
        "model_loaded": _model is not None,
        "voices": voices,
    }


@app.post("/tts")
def tts(body: TTSRequest) -> Response:
    import torch
    import torchaudio

    model = get_model()
    ref = _resolve_voice(body.voice)
    if body.voice and ref is None:
        raise HTTPException(status_code=404, detail=f"Voice '{body.voice}' not found")
    started = time.perf_counter()
    with _lock:
        with torch.inference_mode():
            kwargs = {"language_id": body.language}
            if ref:
                kwargs["audio_prompt_path"] = ref
            wav = model.generate(body.text, **kwargs)
    elapsed = time.perf_counter() - started

    buffer = io.BytesIO()
    torchaudio.save(buffer, wav.cpu(), model.sr, format="wav")
    return Response(
        content=buffer.getvalue(),
        media_type="audio/wav",
        headers={"X-Synthesis-Seconds": f"{elapsed:.2f}"},
    )
