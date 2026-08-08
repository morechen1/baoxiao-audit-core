from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.explanation import ControlledExplanationService, configured_provider_status

router = APIRouter(prefix="/api/v1/explanations", tags=["explanations"])


@router.get("/provider-status")
def get_provider_status() -> dict[str, object]:
    return configured_provider_status()


@router.get("/{run_id}")
def get_explanation(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    return ControlledExplanationService().show(session, run_id)


@router.get("/{run_id}/artifact")
def get_explanation_artifact(run_id: int, session: Session = Depends(get_db)) -> dict[str, object]:
    return ControlledExplanationService().artifact(session, run_id)


@router.get("/{run_id}/citations")
def get_explanation_citations(
    run_id: int, session: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return ControlledExplanationService().citations(session, run_id)
