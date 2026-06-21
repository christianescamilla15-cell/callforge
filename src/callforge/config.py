from functools import lru_cache

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

    # Ollama (local). llm_primary="ollama" makes it the FIRST responder
    # (e.g. a private uncensored model), with Groq as fallback.
    ollama_enabled: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    llm_primary: str = "groq"  # groq | ollama

    # Optional remote "big brain": an on-demand GPU box (cloud or LAN) running
    # Ollama with a larger uncensored model. When set, it answers FIRST and
    # the local model is the automatic fallback if the box is down.
    ollama_remote_url: str = ""  # e.g. http://1.2.3.4:11434 (empty = off)
    ollama_remote_model: str = ""

    # Speed knobs: keep the model warm in VRAM between turns (no cold reload)
    # and cap generation length so replies can't ramble.
    ollama_keep_alive: str = "30m"
    ollama_num_predict: int = 512
    # Context window. Ollama silently defaults to 4096 no matter what the model
    # supports, which truncates the companion's injected memories + history.
    # 8192 fits the 30B-A3B on a 24GB card (16384 OOMs it; bump it on a 48GB box).
    ollama_num_ctx: int = 8192
    # Sampling temperature. Low (0.3) is right for the JSON support pipeline;
    # the companion wants more warmth/personality, so bump it via .env (~0.6-0.7).
    ollama_temperature: float = 0.3

    # Optional per-persona voice (engine voice name/id), so each personality can
    # sound different. Format: "cinico=carnal,filosofo=sabio". Empty = every
    # persona uses the voice selected in the UI.
    persona_voices: str = ""

    # Companion mode: a single warm conversational turn (no router/quality/
    # escalation JSON pipeline). Correct shape for free conversation; lets the
    # uncensored local model shine without the JSON contracts it is weak at.
    companion_mode: bool = False

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
