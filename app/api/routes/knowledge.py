from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.knowledge import SearchRequest, TrustedKnowledgeSearchService

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


@router.get("/search")
def search_knowledge(
    query: str = Query(default="", max_length=500),
    record_types: list[str] | None = Query(default=None),
    pilot_ids: list[str] | None = Query(default=None),
    authority: str | None = Query(default=None, max_length=500),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    evidence_quality: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    session: Session = Depends(get_db),
) -> dict[str, object]:
    request = SearchRequest(
        query=query,
        record_types=tuple(record_types or ()),
        pilot_ids=tuple(pilot_ids or ()),
        authority=authority,
        date_from=date_from,
        date_to=date_to,
        evidence_quality=tuple(evidence_quality or ()),
        limit=limit,
        offset=offset,
    )
    results = TrustedKnowledgeSearchService().search(session, request)
    return {
        "query": query,
        "count": len(results),
        "limit": limit,
        "offset": offset,
        "results": [result.as_dict() for result in results],
    }
