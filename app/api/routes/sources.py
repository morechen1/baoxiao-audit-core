from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.models import DataSource
from app.schemas import SourceCreate

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", status_code=201)
def create_source(payload: SourceCreate, session: Session = Depends(get_db)) -> dict[str, object]:
    source = DataSource(
        name=payload.name,
        base_url=str(payload.base_url) if payload.base_url else None,
        publisher=payload.publisher,
        source_type=payload.source_type.value,
        enabled=payload.enabled,
        crawl_policy=payload.crawl_policy,
        rate_limit_seconds=payload.rate_limit_seconds,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return {"id": source.id, "name": source.name, "source_type": source.source_type}


@router.get("")
def list_sources(session: Session = Depends(get_db)) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "name": item.name,
            "base_url": item.base_url,
            "publisher": item.publisher,
            "source_type": item.source_type,
            "enabled": item.enabled,
            "rate_limit_seconds": item.rate_limit_seconds,
        }
        for item in session.scalars(select(DataSource).order_by(DataSource.id))
    ]
