from datetime import UTC, datetime

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/version")
def version() -> dict:
    from app.core.config import settings

    return {
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
