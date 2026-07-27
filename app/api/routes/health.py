from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health(session: Session = Depends(get_db)) -> dict[str, object]:
    session.execute(text("SELECT 1"))
    settings = get_settings()
    return {
        "status": "ok",
        "database": "ok",
        "llm_enabled": settings.llm_enabled,
        "embedding_enabled": settings.embedding_enabled,
        "ocr_enabled": settings.ocr_enabled,
    }
