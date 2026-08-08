"""Orchestrate deterministic screening with an optional fail-closed semantic supplement."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Protocol, cast

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import ScreeningRun
from app.services.screening.semantic import (
    SEMANTIC_SCREENING_VERSION,
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
        deterministic_payload_hash = run.run_payload_sha256
        deterministic_finding_hashes = {item.finding_sha256 for item in run.findings}
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
        semantic_findings = [
            item for item in run.findings if item.finding_sha256 not in deterministic_finding_hashes
        ]
        run.run_payload_sha256 = _semantic_run_payload_hash(
            deterministic_payload_hash, semantic_findings
        )
        summary = dict(run.evidence_evaluation_summary_json or {})
        summary["semantic_screening"] = {
            "version": SEMANTIC_SCREENING_VERSION,
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


class _PayloadFinding(Protocol):
    finding_sha256: str
    rule_id: str
    raw_start_offset: int
    raw_end_offset: int
    evidence_status: str


def _semantic_run_payload_hash(
    base_payload_hash: str | None, findings: Sequence[_PayloadFinding]
) -> str:
    """Bind semantic supplements into the completed run's integrity payload deterministically."""
    semantic_projection = sorted(
        (
            {
                "finding_sha256": item.finding_sha256,
                "rule_id": item.rule_id,
                "raw_start_offset": item.raw_start_offset,
                "raw_end_offset": item.raw_end_offset,
                "evidence_status": item.evidence_status,
            }
            for item in findings
        ),
        key=lambda value: (
            str(value["finding_sha256"]),
            str(value["rule_id"]),
            cast(int, value["raw_start_offset"]),
        ),
    )
    payload = {
        "base_deterministic_run_payload_sha256": base_payload_hash,
        "semantic_screening_version": SEMANTIC_SCREENING_VERSION,
        "semantic_findings": semantic_projection,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
