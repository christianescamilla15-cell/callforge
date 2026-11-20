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
    await websocket.send_json(
        {"type": "session", "conversation_id": conversation_id, "resumed": resumed}
    )

    try:
        while True:
            text = (await websocket.receive_text()).strip()
            if not text:
                continue
            session = session_factory()
            uow = UnitOfWork(session, tenant_id=tenant_id)
            store = HybridKnowledgeStore(
                uow.knowledge,
                embedder=websocket.app.state.embedder,
                min_similarity=settings.knowledge_min_similarity,
            )
            use_case = SendMessage(uow, websocket.app.state.workflow, store)
            msg = SendMessageInput(conversation_id=conversation_id, content=text[:8000])
            try:
                result = await use_case.execute(msg)
                await websocket.send_json({"type": "reply", **result.__dict__})
            except Exception:  # noqa: BLE001 - keep the socket alive
                logger.exception("webchat message failed")
                await websocket.send_json({"type": "error"})
            finally:
                session.close()
    except WebSocketDisconnect:
        pass
