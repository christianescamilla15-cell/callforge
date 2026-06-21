"""Webchat channel: a WebSocket adapter over the same use cases as REST.
Presentation-only — the workflow and domain are untouched."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from callforge.application.dtos import SendMessageInput, StartConversationInput
from callforge.application.unit_of_work import UnitOfWork
from callforge.application.use_cases.send_message import SendMessage
from callforge.application.use_cases.start_conversation import StartConversation
from callforge.infrastructure.knowledge.store import HybridKnowledgeStore
from callforge.presentation.api.deps import resolve_tenant_id
from callforge.presentation.web.dashboard_page import DASHBOARD_HTML
from callforge.presentation.web.webchat_page import WEBCHAT_HTML

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webchat"])


def _persona_voice(settings, persona: str | None) -> str | None:
    """Resolve the configured voice for a persona ('cinico=carnal,...'), or None."""
    if not persona or not getattr(settings, "persona_voices", ""):
        return None
    for pair in settings.persona_voices.split(","):
        key, _, value = pair.partition("=")
        if key.strip() == persona and value.strip():
            return value.strip()
    return None


@router.get("/webchat", response_class=HTMLResponse, include_in_schema=False)
def webchat_page() -> str:
    return WEBCHAT_HTML


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page() -> str:
    # The page itself is inert; the data endpoints it calls enforce the token.
    return DASHBOARD_HTML


@router.websocket("/webchat/ws")
async def webchat_ws(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    session_factory = websocket.app.state.session_factory

    tenant_id = resolve_tenant_id(
        settings, session_factory, websocket.query_params.get("token")
    )
    if tenant_id is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()

    # Resume an existing conversation on reconnect, otherwise start fresh.
    requested_id = websocket.query_params.get("conversation")
    conversation_id = None
    resumed = False
    session = session_factory()
    try:
        uow = UnitOfWork(session, tenant_id=tenant_id)
        if requested_id:
            existing = uow.conversations.get(requested_id)  # tenant-scoped
            if existing is not None and existing.status.value != "closed":
                conversation_id = existing.id
                resumed = True
        if conversation_id is None:
            conversation_id = (
                StartConversation(uow)
                .execute(StartConversationInput(channel="webchat"))
                .conversation_id
            )
    finally:
        session.close()
    # Proactive opener: on a FRESH companion conversation, greet using memory
    # ("oye, ¿cómo vas con X?") instead of a canned hello. Best-effort.
    opener_text = ""
    if settings.companion_mode and not resumed:
        from callforge.domain.entities import Message
        from callforge.domain.value_objects import AgentName, MessageRole
        from callforge.infrastructure.memory import MemoryStore

        session = session_factory()
        try:
            uow = UnitOfWork(session, tenant_id=tenant_id)
            known = MemoryStore(
                uow.memories, embedder=websocket.app.state.embedder
            ).recent_contents()
            if known:
                try:
                    opener_text = await websocket.app.state.workflow.opening_line(known)
                except Exception:  # noqa: BLE001 - opener is a nicety
                    opener_text = ""
                if opener_text:
                    uow.conversations.add_message(
                        Message(
                            conversation_id=conversation_id,
                            role=MessageRole.ASSISTANT,
                            content=opener_text,
                            agent_name=AgentName.COMPANION,
                            confidence=1.0,
                        )
                    )
                    uow.commit()
        finally:
            session.close()
    await websocket.send_json(
        {"type": "session", "conversation_id": conversation_id, "resumed": resumed,
         "opener": bool(opener_text)}
    )
    if opener_text:
        await websocket.send_json({"type": "reply_chunk", "text": opener_text})
        await websocket.send_json(
            {"type": "reply_done", "reply": opener_text, "agent_used": "companion"}
        )

    try:
        while True:
            text = (await websocket.receive_text()).strip()
            if not text:
                continue
            # Live persona switch ("modo cinico" / "/pensador"), persisted per
            # conversation in app.state. Switch-only messages just acknowledge.
            from callforge.agents.prompts import PERSONAS, parse_persona_command

            personas = getattr(websocket.app.state, "personas", None)
            if personas is None:
                personas = websocket.app.state.personas = {}
            switched, rest = parse_persona_command(text)
            if switched:
                personas[conversation_id] = switched
                if not rest:
                    notice = f"(modo {PERSONAS[switched]['label']} activado)"
                    await websocket.send_json({"type": "reply_chunk", "text": notice})
                    await websocket.send_json(
                        {"type": "reply_done", "reply": notice,
                         "agent_used": "companion", "persona": switched,
                         "voice": _persona_voice(settings, switched)}
                    )
                    continue
                text = rest
            active_persona = personas.get(conversation_id)
            session = session_factory()
            uow = UnitOfWork(session, tenant_id=tenant_id)
            store = HybridKnowledgeStore(
                uow.knowledge,
                embedder=websocket.app.state.embedder,
                min_similarity=settings.knowledge_min_similarity,
            )
            from callforge.infrastructure.memory import MemoryStore

            memory_store = MemoryStore(uow.memories, embedder=websocket.app.state.embedder)
            use_case = SendMessage(
                uow, websocket.app.state.workflow, store, memory_store
            )
            msg = SendMessageInput(
                conversation_id=conversation_id,
                content=text[:8000],
                persona=active_persona,
            )
            try:
                if settings.companion_mode:
                    # Stream sentence by sentence -> client speaks the first one
                    # while the model is still generating the rest.
                    full = []
                    async for sentence in use_case.stream(msg):
                        full.append(sentence)
                        await websocket.send_json(
                            {"type": "reply_chunk", "text": sentence}
                        )
                    await websocket.send_json(
                        {"type": "reply_done", "reply": " ".join(full),
                         "agent_used": "companion", "persona": active_persona,
                         "voice": _persona_voice(settings, active_persona)}
                    )
                else:
                    result = await use_case.execute(msg)
                    await websocket.send_json({"type": "reply", **result.__dict__})
            except Exception:  # noqa: BLE001 - keep the socket alive
                logger.exception("webchat message failed")
                await websocket.send_json({"type": "error"})
            finally:
                session.close()
    except WebSocketDisconnect:
        pass
