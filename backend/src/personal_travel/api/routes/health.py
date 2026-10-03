from fastapi import APIRouter
from sqlalchemy import text

from personal_travel.api.dependencies import SessionDependency

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "travel-api"}


@router.get("/ready")
def ready(session: SessionDependency) -> dict[str, str]:
    """Readiness checks storage; optional external capabilities do not gate CRUD."""
    session.execute(text("SELECT 1"))
    return {"status": "ok", "service": "travel-api"}
