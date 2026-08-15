from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.core.exceptions import BaoxiaoError, DocumentIngestionError
from app.services.audit_export import AuditReportExportService
from app.services.document_ingestion import IngestedMaterial, MarketingDocumentIngestionService
from app.services.screening import HybridScreeningService

router = APIRouter(prefix="/api/v2", tags=["v2-platform"])


@router.post("/screenings/upload")
async def upload_screening(
    file: Annotated[UploadFile, File(...)],
    material_type: Annotated[str, Form()] = "other",
    session: Session = Depends(get_db),
) -> dict[str, object]:
    ingestion = MarketingDocumentIngestionService()
    filename = file.filename or "upload"
    ingestion.validate_content_type(filename, file.content_type)
    content = await file.read(get_settings().max_upload_bytes + 1)
    materials = ingestion.ingest(filename, content)
    if len(materials) != 1:
        raise DocumentIngestionError("upload_single_requires_one_material")
    return _screen(session, materials[0], material_type)


@router.post("/batches")
async def batch_screening(
    files: Annotated[list[UploadFile], File(...)],
    material_type: Annotated[str, Form()] = "other",
    session: Session = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings()
    if not files or len(files) > settings.max_batch_items:
        raise DocumentIngestionError("batch_item_limit_exceeded")
    ingestion = MarketingDocumentIngestionService(settings)
    pending: list[IngestedMaterial] = []
    rejected: list[dict[str, object]] = []
    for file in files:
        try:
            ingestion.validate_content_type(file.filename or "upload", file.content_type)
            content = await file.read(settings.max_upload_bytes + 1)
            pending.extend(ingestion.ingest(file.filename or "upload", content))
        except BaoxiaoError as exc:
            rejected.append(
                {"material": file.filename or "upload", "status": "failed", "error": str(exc)}
            )
        if len(pending) > settings.max_batch_items:
            raise DocumentIngestionError("batch_item_limit_exceeded")

    results: list[dict[str, object]] = []
    for material in pending:
        try:
            results.append(_screen(session, material, material_type))
        except BaoxiaoError as exc:
            results.append(
                {"material": material.source_name, "status": "failed", "error": str(exc)}
            )
    results.extend(rejected)
    succeeded = sum(item.get("status") == "completed" for item in results)
    return {
        "status": "completed" if succeeded == len(results) else "partial",
        "progress": 100,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "items": results,
    }


@router.get("/screenings/{run_id}/reports/json")
def export_json(run_id: int, session: Session = Depends(get_db)) -> JSONResponse:
    report = AuditReportExportService().build(session, run_id)
    return JSONResponse(
        report,
        headers={"Content-Disposition": f'attachment; filename="baoxiao-audit-{run_id}.json"'},
    )


@router.get("/screenings/{run_id}/reports/html")
def export_html(run_id: int, session: Session = Depends(get_db)) -> HTMLResponse:
    service = AuditReportExportService()
    report = service.build(session, run_id)
    return HTMLResponse(
        service.html(report),
        headers={"Content-Disposition": f'attachment; filename="baoxiao-audit-{run_id}.html"'},
    )


def _screen(session: Session, material: IngestedMaterial, material_type: str) -> dict[str, object]:
    service = HybridScreeningService()
    run = service.run(
        session,
        title=material.title,
        material_type=material_type,
        raw_text=material.raw_text,
        source_label=f"upload:{material.source_type}",
        external_reference=material.source_name,
    )
    report = cast(dict[str, Any], service.institution_report(session, run.id))
    summary = report["summary"]
    risk_level = "high" if summary["high_count"] else "medium" if summary["medium_count"] else "low"
    return {
        "material": material.source_name,
        "title": material.title,
        "raw_text": material.raw_text,
        "source_type": material.source_type,
        "status": run.status,
        "screening_run_id": run.id,
        "risk_level": risk_level,
        "finding_count": run.finding_count,
        "primary_rules": summary["matched_rule_ids"],
        "evidence_link_count": report["evidence_summary"]["link_count"],
        "report_endpoints": {
            "detail": f"/api/v1/screenings/{run.id}",
            "institution": f"/api/v1/screenings/{run.id}/institution-report",
            "consumer": f"/api/v1/screenings/{run.id}/consumer-notice",
            "html": f"/api/v2/screenings/{run.id}/reports/html",
            "json": f"/api/v2/screenings/{run.id}/reports/json",
        },
    }
