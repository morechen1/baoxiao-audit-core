from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.services.explanation import ControlledExplanationService

router = APIRouter(prefix="/api/v1/explanations", tags=["explanations"])


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
