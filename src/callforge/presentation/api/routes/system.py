from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from callforge import __version__
from callforge.application.unit_of_work import UnitOfWork
from callforge.config import Settings
from callforge.presentation.api.deps import get_settings_dep, get_uow, require_token
from callforge.presentation.api.schemas import UNAUTHORIZED, HealthResponse, MetricsResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Health check")
def health(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    uow: UnitOfWork = Depends(get_uow),
) -> HealthResponse:
    """Open (no token) for load balancers and post-deploy smoke tests: database
    reachability, the LLM provider chain in order, the app version and, on Render,
    the deployed git commit (RENDER_GIT_COMMIT)."""
    try:
        uow.session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        database = f"error: {exc}"
    providers = [p.name for p in request.app.state.llm_chain.providers]
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        env=settings.env,
        database=database,
        llm_providers=providers,
        version=__version__,
        commit=os.environ.get("RENDER_GIT_COMMIT") or None,
    )


@router.get("/metrics", dependencies=[Depends(require_token)], response_model=MetricsResponse,
            summary="Tenant metrics", responses=UNAUTHORIZED)
def metrics(uow: UnitOfWork = Depends(get_uow)) -> MetricsResponse:
    """Operational snapshot of the caller's tenant only: conversations by status and
    resolution rate, messages, tickets, escalations, agent runs and errors, average
    quality score, LLM usage per provider (calls, tokens, estimated USD cost) and
    customer feedback."""
    return MetricsResponse(**uow.metrics.snapshot())
