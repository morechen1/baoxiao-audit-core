from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.core.exceptions import BaoxiaoError, DocumentIngestionError
from app.schemas.requests import ScreeningCreateRequest
from app.services.platform.batch import BatchReviewService
from app.services.platform.export import PlatformAuditExportService
from app.services.platform.ingestion import MaterialIngestionService, MaterialInput
from app.services.platform.reports import PlatformReportService
from app.services.platform.screening import DETECTION_BASELINE, V1CompatibilityScreeningService
from app.services.platform.store import RUNTIME_STORE

router = APIRouter(prefix="/api/platform", tags=["v1-core-platform"])


@router.post("/screenings/text")
def text_screening(
    request: ScreeningCreateRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    material = MaterialInput(
        title=request.title,
        material_type=request.material_type,
        raw_text=request.raw_text,
        source_filename=request.external_reference or "粘贴文本",
        source_format="text",
        metadata={"source_label": request.source_label},
    )
    result = V1CompatibilityScreeningService().run(session, material)
    payload = PlatformReportService().summary(session, result)
    payload["raw_text"] = material.raw_text
    return payload


@router.post("/screenings/upload")
async def upload_screening(
    file: Annotated[UploadFile, File(...)],
    material_type: Annotated[str, Form()] = "other",
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    filename = file.filename or "upload"
    content = await file.read(settings.max_upload_bytes + 1)
    material = MaterialIngestionService(settings).ingest(
        filename=filename,
        content=content,
        material_type=material_type,
        content_type=file.content_type,
    )
    result = V1CompatibilityScreeningService(settings).run(session, material)
    payload = PlatformReportService().summary(session, result)
    payload["raw_text"] = material.raw_text
    return payload


@router.post("/batches", status_code=202)
async def create_batch(
    background_tasks: BackgroundTasks,
    files: Annotated[list[UploadFile], File(...)],
    material_type: Annotated[str, Form()] = "other",
) -> dict[str, Any]:
    settings = get_settings()
    if not files or len(files) > settings.max_batch_items:
        raise DocumentIngestionError("batch_item_limit_exceeded")
    ingestion = MaterialIngestionService(settings)
    materials = []
    rejected: list[tuple[str, str]] = []
    for file in files:
        filename = file.filename or "upload"
        try:
            content = await file.read(settings.max_upload_bytes + 1)
            materials.append(
                ingestion.ingest(
                    filename=filename,
                    content=content,
                    material_type=material_type,
                    content_type=file.content_type,
                )
            )
        except BaoxiaoError as exc:
            rejected.append((filename, str(exc)))
    service = BatchReviewService(settings)
    record = service.create(materials, rejected)
    background_tasks.add_task(service.process, record.batch_id)
    return service.payload(record)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    return BatchReviewService().payload(RUNTIME_STORE.batch(batch_id))


@router.get("/results/{result_id}")
def get_result(result_id: str, session: Session = Depends(get_db)) -> dict[str, Any]:
    return PlatformReportService().detail(session, RUNTIME_STORE.result(result_id))


@router.get("/results/{result_id}/institution-report")
def get_institution_report(
    result_id: str, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return PlatformReportService().institution(session, RUNTIME_STORE.result(result_id))


@router.get("/results/{result_id}/consumer-notice")
def get_consumer_notice(
    result_id: str, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return PlatformReportService().consumer(session, RUNTIME_STORE.result(result_id))


@router.get("/results/{result_id}/reports/json")
def export_json(result_id: str, session: Session = Depends(get_db)) -> JSONResponse:
    result = RUNTIME_STORE.result(result_id)
    report = PlatformAuditExportService().build(session, result)
    return JSONResponse(
        report,
        headers={"Content-Disposition": _disposition(result.material.title, "json")},
    )


@router.get("/results/{result_id}/reports/html")
def export_html(result_id: str, session: Session = Depends(get_db)) -> HTMLResponse:
    result = RUNTIME_STORE.result(result_id)
    service = PlatformAuditExportService()
    report = service.build(session, result)
    return HTMLResponse(
        service.html(report),
        headers={"Content-Disposition": _disposition(result.material.title, "html")},
    )


@router.get("/runtime")
def runtime_info() -> dict[str, Any]:
    settings = get_settings()
    return {
        "detection_baseline": DETECTION_BASELINE,
        "semantic_screening_enabled": settings.semantic_screening_enabled,
        "semantic_parser_enabled": settings.semantic_parser_enabled,
        "parser_cache_enabled": settings.semantic_parser_cache_enabled,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_batch_items": settings.max_batch_items,
        "batch_concurrency": settings.platform_batch_concurrency,
        "long_document_chunk_chars": settings.platform_long_chunk_chars,
        "ocr_enabled": False,
    }


def _disposition(title: str, extension: str) -> str:
    safe = "".join(character for character in title if character.isalnum() or character in "-_ ")
    filename = f"审核报告_{safe[:80] or '材料'}.{extension}"
    return f"attachment; filename*=UTF-8''{quote(filename)}"
