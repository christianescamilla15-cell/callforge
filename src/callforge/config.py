from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "development"
    database_url: str = "sqlite:///./support_agents.db"

    # Optional API token. If set, every request must send X-API-Token.
    # Leave empty for open local development.
    api_token: str = ""

    # Admin token for tenant management (X-Admin-Token). Empty = admin API off.
    admin_token: str = ""

    # Groq (remote, primary when key present)
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Ollama (local). llm_primary="ollama" makes the local stack the FIRST
    # responder (data stays on-premise, zero cost), with Groq as fallback.
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    llm_primary: str = "groq"  # groq | ollama

    # Optional remote Ollama: an on-demand GPU box (cloud or LAN) serving a
    # larger model (e.g. the teacher used to generate the refund dataset, see
    # FINETUNE_RUNBOOK.md). When set, it answers FIRST and the local model is
    # the automatic fallback if the box is down.
    ollama_remote_url: str = ""  # e.g. http://1.2.3.4:11434 (empty = off)
    ollama_remote_model: str = ""

    # Speed knobs: keep the model warm in VRAM between turns (no cold reload)
    # and cap generation length so replies can't ramble.
    ollama_keep_alive: str = "30m"
    ollama_num_predict: int = 512
    # Context window. Ollama silently defaults to 4096 no matter what the model
    # supports, which truncates long histories + knowledge context.
    ollama_num_ctx: int = 8192
    # Sampling temperature. Low (0.3) keeps the JSON contracts of the support
    # pipeline stable.
    ollama_temperature: float = 0.3

    # Mock (deterministic, final safety net / tests)
    mock_enabled: bool = True

    # Voice: local Kokoro TTS (CPU, zero VRAM) + Groq Whisper STT.
    # tts_engine "chatterbox" tries the cloning micro-service (:8002) first
    # and falls back to Kokoro automatically.
    tts_enabled: bool = True
    tts_engine: str = "kokoro"  # kokoro | kokoro_cloned | chatterbox | cartesia
    voice_convert_url: str = "http://127.0.0.1:8002/convert"

    # Cartesia cloud TTS (cuasi-human, voice cloning). Primary when engine
    # is "cartesia"; degrades to local Kokoro on any failure.
    cartesia_api_key: str = ""
    cartesia_voice_id: str = ""  # stock Spanish voice id, or your cloned id
    cartesia_model: str = "sonic-3.5"
    cartesia_speed: float | None = None  # 0.6-1.5; <1 = slower (empathetic tone)
    voice_server_url: str = "http://127.0.0.1:8002"
    voices_dir: str = "voices"
    ffmpeg_path: str = (
        r"C:\Users\DANNY\dev\tools\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"
    )
    tts_cache_dir: str = "cache/tts"

    # Incoming voice messages are persisted here (cloning references / QA).
    voice_inbox_enabled: bool = True
    voice_inbox_dir: str = "data/voice_inbox"
    chatterbox_url: str = "http://127.0.0.1:8002"
    chatterbox_voice: str = ""  # filename stem inside voices/ (empty = default voice)
    tts_model_path: str = "models/kokoro-v1.0.onnx"
    tts_voices_path: str = "models/voices-v1.0.bin"
    tts_voice: str = "ef_dora"
    tts_lang: str = "es"
    tts_speed: float = 1.0
    stt_model: str = "whisper-large-v3-turbo"
    stt_language: str = "es"

    # Local embeddings for knowledge retrieval (requires Ollama)
    embeddings_enabled: bool = True
    embedding_model: str = "nomic-embed-text"
    # Empirical floor for nomic-embed-text: relevant pairs score ~0.7+,
    # unrelated pairs ~0.45-0.52 (probed live 2026-06-10).
    knowledge_min_similarity: float = 0.55

    llm_timeout_seconds: float = 30.0

    # Workflow policies
    quality_threshold: float = 0.5
    confidence_threshold: float = 0.4
    max_quality_retries: int = 1
    knowledge_top_k: int = 3

    # Engine of the support turn, behind the same application port. "state_machine" (default) is the
    # explicit SupportWorkflow, framework-free; "langgraph" is the opt-in adapter (requirements-langgraph.txt,
    # ADR 0005), with parity measured by tests/test_paridad_motores.py.
    workflow_engine: Literal["state_machine", "langgraph"] = "state_machine"


@lru_cache
def get_settings() -> Settings:
    return Settings()
