from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import ScreeningError
from app.models import MarketingMaterial, MaterialSegment, RiskFinding, ScreeningRun
from app.models.enums import (
    FindingEvidenceStatus,
    MarketingMaterialType,
    ScreeningStatus,
)
from app.services.screening.engine import DeterministicComplianceRuleEngine
from app.services.screening.evidence import RETRIEVAL_VERSION, FindingEvidenceAssembler
from app.services.screening.normalization import NORMALIZATION_VERSION, normalize_marketing_text
from app.services.screening.reports import ScreeningReportService
from app.services.screening.rules import MarketingRuleSet, load_ruleset
from app.services.screening.segmenter import SEGMENTER_VERSION, segment_marketing_text

MAX_RAW_TEXT_LENGTH = 100_000
MAX_TITLE_LENGTH = 300


class DeterministicScreeningService:
    def __init__(
        self,
        ruleset: MarketingRuleSet | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.ruleset = ruleset or load_ruleset()
        self.rule_by_id = {rule.rule_id: rule for rule in self.ruleset.rules}
        self.evidence = FindingEvidenceAssembler(settings)
        self.reports = ScreeningReportService(self.ruleset)

    def run(
        self,
        session: Session,
        *,
        title: str,
        material_type: str,
        raw_text: str,
        source_label: str,
        external_reference: str | None = None,
        is_constructed_evaluation: bool = False,
    ) -> ScreeningRun:
        self._validate_input(title, material_type, raw_text, source_label, external_reference)
        normalized = normalize_marketing_text(raw_text)
        input_payload = {
            "title": title,
            "material_type": material_type,
            "raw_text": raw_text,
            "source_label": source_label,
            "external_reference": external_reference,
            "is_constructed_evaluation": is_constructed_evaluation,
            "normalization_version": NORMALIZATION_VERSION,
        }
        input_sha = _sha(input_payload)
        candidates = segment_marketing_text(raw_text, input_sha)
        if not candidates:
            raise ScreeningError("screening_material_empty")
        index_snapshot = self.evidence.verify_trusted_index(session)
        try:
            material = session.scalar(
                select(MarketingMaterial)
                .where(MarketingMaterial.input_sha256 == input_sha)
                .order_by(MarketingMaterial.id)
            )
            if material is None:
                material = MarketingMaterial(
                    external_reference=external_reference,
                    title=title,
                    material_type=material_type,
                    raw_text=raw_text,
                    normalized_text=normalized.text,
                    normalization_version=NORMALIZATION_VERSION,
                    input_sha256=input_sha,
                    source_label=source_label,
                    is_constructed_evaluation=is_constructed_evaluation,
                )
                session.add(material)
                session.flush()
                segments = [
                    MaterialSegment(
                        material_id=material.id,
                        ordinal=value.ordinal,
                        text=value.text,
                        normalized_text=value.normalized_text,
                        raw_start_offset=value.raw_start_offset,
                        raw_end_offset=value.raw_end_offset,
                        segment_sha256=value.segment_sha256,
                        segmenter_version=SEGMENTER_VERSION,
                    )
                    for value in candidates
                ]
                session.add_all(segments)
                session.flush()
            else:
                segments = sorted(material.segments, key=lambda value: value.ordinal)
                if [value.segment_sha256 for value in segments] != [
                    value.segment_sha256 for value in candidates
                ]:
                    raise ScreeningError("screening_offset_mapping_failed")
            run = ScreeningRun(
                material_id=material.id,
                ruleset_version=self.ruleset.ruleset_version,
                ruleset_sha256=self.ruleset.sha256,
                retrieval_version=RETRIEVAL_VERSION,
                trusted_index_payload_hash=index_snapshot.payload_hash,
                status=ScreeningStatus.RUNNING.value,
            )
            session.add(run)
            session.flush()
            finding_candidates = DeterministicComplianceRuleEngine(self.ruleset).run(
                raw_text=raw_text,
                material_sha256=input_sha,
                segments=candidates,
            )
            segment_by_ordinal = {segment.ordinal: segment for segment in segments}
            findings: list[RiskFinding] = []
            evidence_identities: dict[str, list[str]] = {}
            for candidate in finding_candidates:
                finding = RiskFinding(
                    screening_run_id=run.id,
                    segment_id=segment_by_ordinal[candidate.segment_ordinal].id,
                    rule_id=candidate.rule_id,
                    category=candidate.category,
                    severity=candidate.severity,
                    signal_strength=candidate.signal_strength,
                    matched_text=candidate.matched_text,
                    raw_start_offset=candidate.raw_start_offset,
                    raw_end_offset=candidate.raw_end_offset,
                    normalized_match=candidate.normalized_match,
                    explanation=candidate.explanation,
                    review_question=candidate.review_question,
                    evidence_status=FindingEvidenceStatus.EVIDENCE_INSUFFICIENT.value,
                    finding_sha256=candidate.finding_sha256,
                )
                session.add(finding)
                session.flush()
                links = self.evidence.assemble(session, finding, self.rule_by_id[candidate.rule_id])
                session.add_all(links)
                evidence_identities[finding.finding_sha256] = sorted(
                    link.chunk_identity_sha256 for link in links
                )
                findings.append(finding)
            session.flush()
            run.finding_count = len(findings)
            run.insufficient_evidence_count = sum(
                finding.evidence_status == FindingEvidenceStatus.EVIDENCE_INSUFFICIENT.value
                for finding in findings
            )
            run.run_payload_sha256 = _sha(
                {
                    "material_sha256": input_sha,
                    "ruleset_sha256": self.ruleset.sha256,
                    "trusted_index_payload_hash": index_snapshot.payload_hash,
                    "findings": [
                        {
                            "finding_sha256": finding.finding_sha256,
                            "evidence_status": finding.evidence_status,
                            "evidence": evidence_identities[finding.finding_sha256],
                        }
                        for finding in findings
                    ],
                }
            )
            run.status = ScreeningStatus.COMPLETED.value
            run.completed_at = datetime.now(UTC)
            session.commit()
            return run
        except ScreeningError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise ScreeningError("screening_persistence_failed") from exc

    def show(self, session: Session, run_id: int) -> dict[str, object]:
        return self.reports.run_detail(session, run_id)

    def institution_report(self, session: Session, run_id: int) -> dict[str, object]:
        return self.reports.institution_report(session, run_id)

    def consumer_notice(self, session: Session, run_id: int) -> dict[str, object]:
        return self.reports.consumer_notice(session, run_id)

    @staticmethod
    def _validate_input(
        title: str,
        material_type: str,
        raw_text: str,
        source_label: str,
        external_reference: str | None,
    ) -> None:
        if not title.strip() or len(title) > MAX_TITLE_LENGTH:
            raise ScreeningError("screening_title_invalid")
        if material_type not in {value.value for value in MarketingMaterialType}:
            raise ScreeningError("screening_material_type_invalid")
        if not raw_text.strip():
            raise ScreeningError("screening_material_empty")
        if len(raw_text) > MAX_RAW_TEXT_LENGTH:
            raise ScreeningError("screening_material_too_large")
        if not source_label.strip() or len(source_label) > 255:
            raise ScreeningError("screening_source_label_invalid")
        if external_reference is not None and len(external_reference) > 255:
            raise ScreeningError("screening_external_reference_invalid")


def _sha(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
