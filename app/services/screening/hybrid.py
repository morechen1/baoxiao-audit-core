"""Orchestrate deterministic screening with an optional fail-closed semantic supplement."""
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ScreeningRun
from app.services.screening.semantic import (
    SemanticScreeningError,
    TrustedRAGSemanticScreeningService,
)
from app.services.screening.service import DeterministicScreeningService


class HybridScreeningService:
    """Preserves deterministic results even when semantic screening is unavailable or rejected."""

    def __init__(
        self,
        settings: Settings | None = None,
        deterministic: DeterministicScreeningService | None = None,
        semantic: TrustedRAGSemanticScreeningService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.deterministic = deterministic or DeterministicScreeningService(settings=self.settings)
        self.semantic = semantic or TrustedRAGSemanticScreeningService(settings=self.settings)

    def run(self, session: Session, **kwargs: Any) -> ScreeningRun:
        run = self.deterministic.run(session, **kwargs)
        if not self.settings.semantic_screening_enabled:
            return run
        try:
            added = self.semantic.enrich(session, run)
        except SemanticScreeningError:
            # A semantic provider/validator failure must never fabricate, erase, or alter
            # deterministic findings. The supplemental path is strictly fail-closed.
            session.rollback()
            return run
        if not added:
            return run
        run.finding_count = len(run.findings)
        summary = dict(run.evidence_evaluation_summary_json or {})
        summary["semantic_screening"] = {
            "version": "trusted_rag_semantic_screening_v1",
            "added": added,
        }
        run.evidence_evaluation_summary_json = summary
        session.commit()
        return run

    def show(self, session: Session, run_id: int) -> dict[str, object]:
        return self.deterministic.show(session, run_id)

    def institution_report(self, session: Session, run_id: int) -> dict[str, object]:
        return self.deterministic.institution_report(session, run_id)

    def consumer_notice(self, session: Session, run_id: int) -> dict[str, object]:
        return self.deterministic.consumer_notice(session, run_id)
