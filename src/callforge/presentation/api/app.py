from __future__ import annotations

import logging

from fastapi import FastAPI

from callforge import __version__
from callforge.agents.escalation_agent import EscalationAgent
from callforge.agents.quality_agent import QualityAgent
from callforge.agents.router_agent import RouterAgent
from callforge.agents.summarizer_agent import SummarizerAgent
from callforge.agents.support_agent import SupportAgent
from callforge.agents.troubleshooting_agent import TroubleshootingAgent
from callforge.config import Settings, get_settings
from callforge.infrastructure.database import (
    build_engine,
    build_session_factory,
    run_migrations,
)
from callforge.infrastructure.knowledge.embeddings import OllamaEmbeddingProvider
from callforge.infrastructure.llm.fallback import build_provider_chain
from callforge.orchestration.policies import EscalationPolicy
from callforge.orchestration.workflow import SupportWorkflow
from callforge.presentation.api.routes import (
    admin,
    companion,
    conversations,
    feedback,
    knowledge,
    system,
    tickets,
    voice,
    webchat,
)

logging.basicConfig(level=logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    engine = build_engine(settings.database_url)
    run_migrations(engine)
    session_factory = build_session_factory(engine)

    llm_chain = build_provider_chain(settings)
    workflow = SupportWorkflow(
        router=RouterAgent(llm_chain),
        support=SupportAgent(llm_chain),
        troubleshooting=TroubleshootingAgent(llm_chain),
        escalation=EscalationAgent(llm_chain),
        summarizer=SummarizerAgent(llm_chain),
        quality=QualityAgent(llm_chain),
        policy=EscalationPolicy.from_settings(settings),
        knowledge_top_k=settings.knowledge_top_k,
        companion_mode=settings.companion_mode,
    )

    app = FastAPI(
        title="CallForge",
        version=__version__,
        description="Multi-agent customer support automation platform",
    )
    embedder = None
    if settings.embeddings_enabled and settings.ollama_enabled:
        embedder = OllamaEmbeddingProvider(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout=settings.llm_timeout_seconds,
        )

    tts = None
    if settings.tts_enabled:
        from callforge.infrastructure.voice.tts import (
            CachedTTS,
            CartesiaTTS,
            ChatterboxRemoteTTS,
            ClonedLiveTTS,
            FallbackTTS,
            KokoroTTS,
        )

        kokoro = KokoroTTS(
            model_path=settings.tts_model_path,
            voices_path=settings.tts_voices_path,
            voice=settings.tts_voice,
            lang=settings.tts_lang,
            speed=settings.tts_speed,
        )
        if settings.tts_engine == "chatterbox":
            chatterbox = ChatterboxRemoteTTS(
                base_url=settings.chatterbox_url,
                voice=settings.chatterbox_voice or None,
                language=settings.tts_lang,
            )
            base_tts = FallbackTTS([chatterbox, kokoro])
        elif settings.tts_engine == "kokoro_cloned":
            # Live cloned timbre: Kokoro -> OpenVoice converter (:8002),
            # degrading to plain Kokoro inside the engine itself.
            base_tts = ClonedLiveTTS(kokoro, convert_url=settings.voice_convert_url)
        elif settings.tts_engine == "cartesia" and settings.cartesia_api_key:
            # Cloud-primary: Cartesia (cuasi-human) -> local Kokoro safety net.
            cartesia = CartesiaTTS(
                api_key=settings.cartesia_api_key,
                voice_id=settings.cartesia_voice_id,
                model=settings.cartesia_model,
                language=settings.tts_lang,
                speed=settings.cartesia_speed,
            )
            base_tts = FallbackTTS([cartesia, kokoro])
        else:
            base_tts = kokoro
        tts = CachedTTS(base_tts, cache_dir=settings.tts_cache_dir)
    stt = None
    if settings.groq_api_key:
        from callforge.infrastructure.voice.stt import GroqSTT

        stt = GroqSTT(
            api_key=settings.groq_api_key,
            model=settings.stt_model,
            base_url=settings.groq_base_url,
        )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.llm_chain = llm_chain
    app.state.workflow = workflow
    app.state.embedder = embedder
    app.state.tts = tts
    app.state.stt = stt

    api = "/api/v1"
    app.include_router(conversations.router, prefix=api)
    app.include_router(knowledge.router, prefix=api)
    app.include_router(tickets.router, prefix=api)
    app.include_router(feedback.router, prefix=api)
    app.include_router(system.router, prefix=api)
    app.include_router(admin.router, prefix=api)
    app.include_router(voice.router, prefix=api)
    app.include_router(voice.conversation_router, prefix=api)
    app.include_router(companion.router, prefix=api)
    app.include_router(webchat.router)  # page at /webchat, socket at /webchat/ws
    return app


app = create_app()
