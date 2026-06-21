"""Voice endpoints: TTS for any text, and the full voice turn
(audio in -> Groq STT -> agent workflow -> Kokoro TTS -> audio out)."""
from __future__ import annotations

import base64
import re

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from callforge.application.dtos import (
    ConversationClosedError,
    ConversationNotFoundError,
    SendMessageInput,
)
from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.send_message import SendMessage
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore
from callforge.orchestration.workflow import SupportWorkflow
from callforge.presentation.api.deps import (
    get_knowledge_store,
    get_memory_store,
    get_uow,
    get_workflow,
    require_token,
)

router = APIRouter(prefix="/voice", tags=["voice"], dependencies=[Depends(require_token)])
conversation_router = APIRouter(
    prefix="/conversations", tags=["voice"], dependencies=[Depends(require_token)]
)

MAX_AUDIO_BYTES = 15 * 1024 * 1024


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str | None = None


class VoiceMessageResponse(BaseModel):
    transcript: str
    conversation_id: str
    reply: str
    agent_used: str
    intent: str
    category: str
    urgency: str
    confidence: float
    quality_score: float | None
    escalated: bool
    ticket_id: str | None
    conversation_status: str
    audio_b64: str | None
    audio_mime: str = "audio/wav"
    # stream mode: audio_b64 covers only the FIRST sentence; the client
    # fetches the rest via /voice/tts while it plays (low TTFA).
    remaining_sentences: list[str] = []


def _get_tts(request: Request):
    tts = request.app.state.tts
    if tts is None:
        raise HTTPException(status_code=503, detail="TTS disabled (TTS_ENABLED=false)")
    return tts


def _get_stt(request: Request):
    stt = request.app.state.stt
    if stt is None:
        raise HTTPException(
            status_code=503, detail="STT unavailable (set GROQ_API_KEY)"
        )
    return stt


@router.post("/tts")
async def synthesize(body: TTSRequest, request: Request) -> Response:
    tts = _get_tts(request)
    wav = await run_in_threadpool(tts.synthesize, body.text, body.voice)
    return Response(content=wav, media_type="audio/wav")


@router.get("/fillers")
def list_fillers() -> list[str]:
    """Filler texts the client prefetches and plays while the LLM thinks."""
    from callforge.infrastructure.voice.phrases import FILLERS

    return FILLERS


async def _cartesia_voices(request: Request) -> list[dict]:
    """Spanish Cartesia voices as {id, name}, fetched once and cached."""
    import httpx

    cached = getattr(request.app.state, "cartesia_voices", None)
    if cached is not None:
        return cached
    settings = request.app.state.settings
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://api.cartesia.ai/voices",
            headers={
                "Authorization": f"Bearer {settings.cartesia_api_key}",
                "Cartesia-Version": "2026-03-01",
            },
            params={"language": "es", "limit": 100},
        )
        response.raise_for_status()
        data = response.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    voices = [{"id": v["id"], "name": v.get("name", v["id"])} for v in items]
    request.app.state.cartesia_voices = voices
    return voices


@router.get("/voices")
async def list_voices(request: Request) -> dict:
    """Voices for the active engine: Cartesia stock/cloned voices when the
    cloud engine is on, otherwise the local OpenVoice references. Normalized
    to {id, name} objects so the same panel drives both."""
    import httpx

    settings = request.app.state.settings

    if settings.tts_engine == "cartesia" and settings.cartesia_api_key:
        try:
            voices = await _cartesia_voices(request)
            return {
                "default": settings.cartesia_voice_id or None,
                "engine": "cartesia",
                "voices": voices,
            }
        except Exception:  # noqa: BLE001 - fall through to local listing
            pass

    # Local OpenVoice path: proxy the voice server, fall back to voices/ dir.
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{settings.voice_server_url}/voices")
            response.raise_for_status()
            payload = response.json()
        names = payload.get("voices", [])
        return {
            "default": payload.get("default"),
            "engine": "local",
            "voices": [{"id": n, "name": n} for n in names],
        }
    except Exception:  # noqa: BLE001 - degrade to local directory listing
        from pathlib import Path

        voices_dir = Path(settings.voices_dir)
        names = (
            sorted(
                p.stem
                for p in voices_dir.glob("*")
                if p.suffix in (".wav", ".mp3", ".flac")
            )
            if voices_dir.exists()
            else []
        )
        return {
            "default": None,
            "engine": "local",
            "voices": [{"id": n, "name": n} for n in names],
        }


_VOICE_NAME_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


@router.post("/voices", status_code=201)
async def upload_voice(request: Request, file: UploadFile, name: str) -> dict:
    """Clone any voice from 10-60s of clean audio. In Cartesia mode it clones
    in the cloud (cuasi-human, returns a voice id); in local mode it stores a
    normalized WAV reference the OpenVoice converter embeds on first use."""
    import subprocess
    import tempfile
    from pathlib import Path

    settings = request.app.state.settings
    safe = name.strip().lower()
    if not _VOICE_NAME_RE.match(safe):
        raise HTTPException(
            status_code=422,
            detail="Voice name must be 2-32 chars: a-z, 0-9, '_' or '-'",
        )
    audio = await file.read()
    if len(audio) < 50_000:
        raise HTTPException(status_code=422, detail="Audio too short (need ~10s+)")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large")

    # Cartesia mode: clone in the cloud. Cartesia accepts wav/mp3/webm/ogg/...
    # directly, so no ffmpeg step. Returns the new voice id; invalidate the
    # cached voice list so it shows up in the panel immediately.
    if settings.tts_engine == "cartesia" and settings.cartesia_api_key:
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.cartesia.ai/voices/clone",
                headers={
                    "Authorization": f"Bearer {settings.cartesia_api_key}",
                    "Cartesia-Version": "2026-03-01",
                },
                files={"clip": (file.filename or "ref.wav", audio, file.content_type or "audio/wav")},
                data={"name": safe, "language": settings.tts_lang, "description": f"CallForge: {safe}"},
            )
        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=502, detail=f"Cartesia clone failed: {resp.text[:200]}"
            )
        voice = resp.json()
        request.app.state.cartesia_voices = None  # refresh the panel list
        return {"name": voice.get("name", safe), "id": voice["id"], "engine": "cartesia"}

    voices_dir = Path(settings.voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    target = voices_dir / f"{safe}.wav"

    suffix = Path(file.filename or "ref.webm").suffix or ".webm"
    if suffix == ".wav":
        target.write_bytes(audio)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"in{suffix}"
            src.write_bytes(audio)
            result = subprocess.run(
                [
                    settings.ffmpeg_path, "-y", "-loglevel", "error",
                    "-i", str(src), "-ac", "1", "-ar", "24000", str(target),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"Audio conversion failed: {result.stderr.decode()[:200]}",
                )

    # Pre-warm the OpenVoice embedding so the freshly cloned voice is instant
    # on its first use (no extraction lag when you switch to it live).
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(
                f"{settings.voice_server_url}/warm", params={"voice": safe}
            )
    except Exception:  # noqa: BLE001 - warming is best-effort
        pass
    return {"name": safe, "id": safe, "file": target.name, "engine": "local"}


@conversation_router.post(
    "/{conversation_id}/voice-message", response_model=VoiceMessageResponse
)
async def voice_message(
    conversation_id: str,
    file: UploadFile,
    request: Request,
    stream: bool = False,
    voice: str | None = None,
    uow: UnitOfWork = Depends(get_uow),
    workflow: SupportWorkflow = Depends(get_workflow),
    knowledge_store: HybridKnowledgeStore = Depends(get_knowledge_store),
    memory_store=Depends(get_memory_store),
) -> VoiceMessageResponse:
    stt = _get_stt(request)
    settings = request.app.state.settings

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="Empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large")

    # Voice inbox: keep the customer's audio on disk (voice-cloning
    # references and QA). Best-effort, never blocks the turn.
    if settings.voice_inbox_enabled:
        try:
            from datetime import datetime, timezone
            from pathlib import Path

            inbox = Path(settings.voice_inbox_dir)
            inbox.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            suffix = Path(file.filename or "audio.webm").suffix or ".webm"
            (inbox / f"{stamp}-{conversation_id[:8]}{suffix}").write_bytes(audio)
        except OSError:
            pass

    transcript = await stt.transcribe(
        audio, file.filename or "audio.webm", language=settings.stt_language
    )
    if not transcript:
        raise HTTPException(status_code=422, detail="Could not transcribe audio")

    try:
        result = await SendMessage(uow, workflow, knowledge_store, memory_store).execute(
            SendMessageInput(conversation_id=conversation_id, content=transcript)
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    except ConversationClosedError:
        raise HTTPException(status_code=409, detail="Conversation is closed")

    audio_b64 = None
    remaining: list[str] = []
    tts = request.app.state.tts
    if tts is not None:
        from callforge.infrastructure.voice.tts import split_for_tts

        sentences = split_for_tts(result.reply)
        to_speak = sentences[0] if stream and len(sentences) > 1 else result.reply
        if stream and len(sentences) > 1:
            remaining = sentences[1:]
        try:
            wav = await run_in_threadpool(tts.synthesize, to_speak, voice)
            audio_b64 = base64.b64encode(wav).decode("ascii")
        except Exception:  # noqa: BLE001 - voice reply is best-effort
            audio_b64 = None
            remaining = []

    return VoiceMessageResponse(
        transcript=transcript,
        conversation_id=result.conversation_id,
        reply=result.reply,
        agent_used=result.agent_used,
        intent=result.intent,
        category=result.category,
        urgency=result.urgency,
        confidence=result.confidence,
        quality_score=result.quality_score,
        escalated=result.escalated,
        ticket_id=result.ticket_id,
        conversation_status=result.conversation_status,
        audio_b64=audio_b64,
        remaining_sentences=remaining,
    )
