from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas.requests import ScreeningCreateRequest
from app.services.screening import DeterministicScreeningService

router = APIRouter(prefix="/api/v1/screenings", tags=["screenings"])


@router.post("")
def create_screening(
    request: ScreeningCreateRequest,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    service = DeterministicScreeningService()
    run = service.run(
        session,
        title=request.title,
        material_type=request.material_type,
        raw_text=request.raw_text,
        source_label=request.source_label,
        external_reference=request.external_reference,
    )
    return {
        "material_id": run.material_id,
        "screening_run_id": run.id,
        "status": run.status,
        "finding_count": run.finding_count,
        "report_endpoints": {
            "screening": f"/api/v1/screenings/{run.id}",
            "institution_report": f"/api/v1/screenings/{run.id}/institution-report",
            "consumer_notice": f"/api/v1/screenings/{run.id}/consumer-notice",
        },
    }


@router.get("/{run_id}")
def get_screening(
    run_id: int,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return DeterministicScreeningService().show(session, run_id)


@router.get("/{run_id}/institution-report")
def get_institution_report(
    run_id: int,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return DeterministicScreeningService().institution_report(session, run_id)


@router.get("/{run_id}/consumer-notice")
def get_consumer_notice(
    run_id: int,
    session: Session = Depends(get_db),
) -> dict[str, object]:
    return DeterministicScreeningService().consumer_notice(session, run_id)
