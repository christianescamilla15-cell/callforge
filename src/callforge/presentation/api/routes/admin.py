from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from callforge.application.unit_of_work import UnitOfWork
from callforge.domain.entities import Tenant
from callforge.presentation.api.deps import get_uow, require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class TenantResponse(BaseModel):
    id: str
    name: str
    api_key: str


@router.post("/tenants", response_model=TenantResponse, status_code=201)
def create_tenant(
    body: CreateTenantRequest, uow: UnitOfWork = Depends(get_uow)
) -> TenantResponse:
    tenant = Tenant(name=body.name, api_key=secrets.token_hex(24))
    uow.tenants.add(tenant)
    uow.commit()
    return TenantResponse(id=tenant.id, name=tenant.name, api_key=tenant.api_key)
