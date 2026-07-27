from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.repositories import DocumentRepository
from app.schemas import (
    CollectionLocalRequest,
    CollectionUrlRequest,
    ReviewBatchRequest,
    ReviewImportRequest,
)
from app.services.collection import FileCollector, WebPageCollector
from app.services.knowledge import KnowledgeIndexService
from app.services.parsing import ParsingService
from app.services.review import ReviewService
from app.services.validation import ValidationService

router = APIRouter(tags=["workflow"])


@router.post("/collection/url")
def collect_url(
    payload: CollectionUrlRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    collector = WebPageCollector()
    result = collector.collect(str(payload.url))
    document, created = collector.persist(
        session,
        result,
        payload.source_type.value,
        source_id=payload.source_id,
        authenticity_type=payload.authenticity_type.value,
    )
    return {"document_id": document.id, "created": created, "sha256": document.sha256}


@router.post("/collection/local")
def collect_local(
    payload: CollectionLocalRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    path = _safe_data_path(payload.path)
    collector = FileCollector()
    result = collector.collect(path)
    document, created = collector.persist(
        session,
        result,
        payload.source_type.value,
        source_id=payload.source_id,
        authenticity_type=payload.authenticity_type.value,
    )
    return {"document_id": document.id, "created": created, "sha256": document.sha256}


@router.post("/parsing/run")
def parse_pending(session: Session = Depends(get_db)) -> dict[str, object]:
    parsed, errors = ParsingService().parse_pending(session)
    return {"parsed": parsed, "errors": errors}


@router.post("/validation/run")
def validate_pending(session: Session = Depends(get_db)) -> dict[str, int]:
    passed, failed = ValidationService().validate_pending(session)
    return {"passed": passed, "failed": failed}


@router.post("/review/batches")
def export_review_batch(
    payload: ReviewBatchRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    batch = ReviewService().export_batch(session, payload.data_type.value, payload.format)
    return {"batch_id": batch.id, "record_count": batch.record_count, "path": batch.export_path}


@router.post("/review/results/import")
def import_review_results(
    payload: ReviewImportRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    path = _safe_data_path(payload.path, required_subdir="review_results")
    imported, errors = ReviewService().import_results(session, path, payload.batch_id)
    return {"imported": imported, "errors": errors}


@router.get("/records")
def list_records(
    status: str | None = Query(default=None),
    data_type: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [_document_dict(item) for item in DocumentRepository(session).list(status, data_type)]


@router.get("/records/{record_type}/{record_id}")
def get_record(
    record_type: str, record_id: int, session: Session = Depends(get_db)
) -> dict[str, object]:
    document = DocumentRepository(session).get(record_id)
    if not document or document.data_type != record_type:
        raise HTTPException(status_code=404, detail="Record not found")
    return _document_dict(document, include_text=True)


@router.post("/knowledge/index-approved")
def index_approved(session: Session = Depends(get_db)) -> dict[str, int]:
    return {"indexed": KnowledgeIndexService().index_approved(session)}


def _safe_data_path(path: Path, required_subdir: str | None = None) -> Path:
    data_dir = get_settings().data_dir.resolve()
    candidate = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    expected_root = data_dir / required_subdir if required_subdir else data_dir
    if candidate != expected_root and expected_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path is outside the allowed data directory")
    return candidate


def _document_dict(document: object, include_text: bool = False) -> dict[str, object]:
    result = {
        "id": document.id,
        "data_type": document.data_type,
        "source_url": document.source_url,
        "source_title": document.source_title,
        "authenticity_type": document.authenticity_type,
        "final_review_status": document.final_review_status,
        "knowledge_index_status": document.knowledge_index_status,
        "corrected_fields": document.corrected_fields_json,
    }
    if include_text:
        result["raw_text"] = document.raw_text
        result["metadata"] = document.metadata_json
    return result
