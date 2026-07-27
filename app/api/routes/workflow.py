from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.models import DataSource, Regulation, RegulatoryCase, SourceDocument
from app.models.enums import (
    AuthenticityType,
    DataType,
    RegulatoryCaseCategory,
    RegulatoryCaseUsage,
)
from app.repositories import DocumentRepository
from app.schemas import (
    CollectionLocalRequest,
    CollectionUrlRequest,
    ReviewBatchRequest,
    ReviewImportRequest,
    StructuredImportRequest,
)
from app.services.collection import FileCollector, SafeUrlPolicy, WebPageCollector
from app.services.knowledge import KnowledgeIndexService
from app.services.parsing import ParsingService
from app.services.review import ReviewService
from app.services.structured_records import StructuredRecordService
from app.services.validation import ValidationService

router = APIRouter(tags=["workflow"])


@router.post("/collection/url")
def collect_url(
    payload: CollectionUrlRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    source = session.get(DataSource, payload.source_id)
    if not source or not source.enabled:
        raise HTTPException(status_code=400, detail="source_id must identify an enabled source")
    if source.source_type != payload.source_type.value:
        raise HTTPException(status_code=400, detail="source_type does not match registered source")
    allowed_domains = source.crawl_policy.get("allowed_domains", [])
    if (
        not isinstance(allowed_domains, list)
        or any(not isinstance(value, str) for value in allowed_domains)
        or not SafeUrlPolicy.host_allowed(str(payload.url), source.base_url, allowed_domains)
    ):
        raise HTTPException(status_code=400, detail="URL host is not allowed for this source")
    allowed_hosts = SafeUrlPolicy.allowed_hosts(source.base_url, allowed_domains)
    collector = WebPageCollector(allowed_hosts=allowed_hosts)
    result = collector.collect(str(payload.url))
    document, created = collector.persist(
        session,
        result,
        payload.source_type.value,
        source_id=payload.source_id,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
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
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
    )
    return {"document_id": document.id, "created": created, "sha256": document.sha256}


@router.post("/parsing/run")
def parse_pending(session: Session = Depends(get_db)) -> dict[str, object]:
    parsed, requires_ocr, errors = ParsingService().parse_pending(session)
    return {"parsed": parsed, "requires_ocr": requires_ocr, "errors": errors}


@router.post("/validation/run")
def validate_pending(session: Session = Depends(get_db)) -> dict[str, int]:
    passed, failed = ValidationService().validate_pending(session)
    return {"passed": passed, "failed": failed}


@router.post("/review/batches")
def export_review_batch(
    payload: ReviewBatchRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    batch = ReviewService().export_batch(session, payload.data_type.value, payload.format)
    return {
        "batch_id": batch.id,
        "record_count": batch.record_count,
        "path": batch.export_path,
        "export_sha256": batch.export_sha256,
        "schema_version": batch.schema_version,
    }


@router.post("/review/results/import")
def import_review_results(
    payload: ReviewImportRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    path = _safe_data_path(payload.path, required_subdir="review_results")
    imported, errors = ReviewService().import_results(session, path, payload.batch_id)
    return {"imported": imported, "errors": errors}


@router.post("/structured-drafts/import")
def import_structured_drafts(
    payload: StructuredImportRequest, session: Session = Depends(get_db)
) -> dict[str, object]:
    path = _safe_data_path(payload.path, required_subdir="parsed")
    imported, errors = StructuredRecordService().import_jsonl(session, path)
    return {"imported": imported, "errors": errors}


@router.get("/records")
def list_records(
    status: str | None = Query(default=None),
    data_type: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [
        _document_dict(item, session)
        for item in DocumentRepository(session).list(status, data_type)
    ]


@router.get("/records/{record_type}/{record_id}")
def get_record(
    record_type: str, record_id: int, session: Session = Depends(get_db)
) -> dict[str, object]:
    document = DocumentRepository(session).get(record_id)
    if not document or document.data_type != record_type:
        raise HTTPException(status_code=404, detail="Record not found")
    return _document_dict(document, session, include_text=True)


@router.get("/regulatory-cases")
def list_regulatory_cases(
    case_category: RegulatoryCaseCategory | None = Query(default=None),
    case_usage: RegulatoryCaseUsage | None = Query(default=None),
    review_status: str | None = Query(default=None),
    authenticity_type: AuthenticityType | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[dict[str, object]]:
    statement = (
        select(RegulatoryCase, SourceDocument)
        .join(SourceDocument, SourceDocument.id == RegulatoryCase.document_id)
        .order_by(RegulatoryCase.id)
    )
    if case_category is not None:
        statement = statement.where(RegulatoryCase.case_category == case_category.value)
    if case_usage is not None:
        statement = statement.where(RegulatoryCase.case_usage == case_usage.value)
    if review_status is not None:
        statement = statement.where(RegulatoryCase.final_review_status == review_status)
    if authenticity_type is not None:
        statement = statement.where(SourceDocument.authenticity_type == authenticity_type.value)
    return [
        _regulatory_case_dict(record, document, session)
        for record, document in session.execute(statement)
    ]


@router.get("/regulatory-cases/{case_id}")
def get_regulatory_case(
    case_id: int,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    record = session.get(RegulatoryCase, case_id)
    if record is None:
        raise HTTPException(status_code=404, detail="RegulatoryCase not found")
    document = session.get(SourceDocument, record.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="RegulatoryCase document not found")
    return _regulatory_case_dict(record, document, session)


@router.post("/knowledge/index-approved")
def index_approved(session: Session = Depends(get_db)) -> dict[str, object]:
    summary = KnowledgeIndexService().index_approved(session)
    return {"indexed": summary.indexed, "rejected": summary.rejected}


def _safe_data_path(path: Path, required_subdir: str | None = None) -> Path:
    data_dir = get_settings().data_dir.resolve()
    candidate = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    expected_root = data_dir / required_subdir if required_subdir else data_dir
    if candidate != expected_root and expected_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path is outside the allowed data directory")
    return candidate


def _document_dict(
    document: SourceDocument,
    session: Session,
    include_text: bool = False,
) -> dict[str, object]:
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
    if document.data_type == DataType.REGULATION.value:
        statuses = {
            regulation.validity_status or "unknown"
            for regulation in session.query(Regulation).filter_by(document_id=document.id)
        }
        result["regulation_validity_status"] = (
            next(iter(statuses)) if len(statuses) == 1 else "unknown"
        )
        result["regulation_validity_display"] = "效力状态待核验"
    if document.data_type == DataType.REGULATORY_CASE.value:
        record = session.scalar(
            select(RegulatoryCase).where(RegulatoryCase.document_id == document.id)
        )
        if record is not None:
            result["regulatory_case"] = _regulatory_case_dict(record, document, session)
    if include_text:
        result["raw_text"] = document.raw_text
        result["metadata"] = document.metadata_json
    return result


def _regulatory_case_dict(
    record: RegulatoryCase,
    document: SourceDocument,
    session: Session,
) -> dict[str, object]:
    rejection_reasons = KnowledgeIndexService().rejection_reasons(session, document)
    return {
        "id": record.id,
        "document_id": document.id,
        "case_title": record.case_title,
        "publisher": record.publisher,
        "published_at": record.published_at,
        "case_category": record.case_category,
        "scenario_text": record.scenario_text,
        "marketing_wording_disclosed": record.marketing_wording_disclosed,
        "marketing_wording": record.marketing_wording,
        "case_facts": record.case_facts,
        "regulatory_analysis": record.regulatory_analysis,
        "consumer_advice": record.consumer_advice,
        "case_usage": record.case_usage,
        "source_quote": record.source_quote,
        "field_evidence": record.field_evidence_json,
        "evidence_quality": record.evidence_quality,
        "final_review_status": record.final_review_status,
        "authenticity_type": document.authenticity_type,
        "knowledge_index_status": document.knowledge_index_status,
        "can_index": not rejection_reasons,
        "index_rejection_reasons": rejection_reasons,
    }
