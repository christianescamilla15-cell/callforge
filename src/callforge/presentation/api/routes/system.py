from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from callforge.application.unit_of_work import UnitOfWork
from callforge.config import Settings
from callforge.presentation.api.deps import get_settings_dep, get_uow, require_token
from callforge.presentation.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    uow: UnitOfWork = Depends(get_uow),
) -> HealthResponse:
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
    )


@router.get("/metrics", dependencies=[Depends(require_token)])
def metrics(uow: UnitOfWork = Depends(get_uow)) -> dict:
    return uow.metrics.snapshot()
