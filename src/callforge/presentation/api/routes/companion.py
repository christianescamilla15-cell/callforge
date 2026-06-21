from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from callforge.application.unit_of_work import UnitOfWork
from callforge.presentation.api.deps import get_uow, require_token

router = APIRouter(
    prefix="/companion", tags=["companion"], dependencies=[Depends(require_token)]
)


@router.get("/personas")
def list_personas() -> dict:
    """Available companion personas/modes (switch live with 'modo X' or '/X')."""
    from callforge.agents.prompts import persona_list

    return {"personas": persona_list()}


@router.get("/memories")
def list_memories(limit: int = 100, uow: UnitOfWork = Depends(get_uow)) -> dict:
    """What the companion remembers about you (most recent first), with ids so
    a wrong/confabulated memory can be corrected via DELETE."""
    memories = uow.memories.list_all()
    return {
        "count": len(memories),
        "memories": [
            {"id": m.id, "content": m.content} for m in reversed(memories[-limit:])
        ],
    }


@router.delete("/memories/{memory_id}")
def forget_memory(memory_id: str, uow: UnitOfWork = Depends(get_uow)) -> dict:
    """Forget a single memory (e.g. one the companion got wrong)."""
    deleted = uow.memories.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    uow.commit()
    return {"deleted": memory_id}
