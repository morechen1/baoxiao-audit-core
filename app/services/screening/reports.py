from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ScreeningError
from app.models import MarketingMaterial, RiskFinding, ScreeningRun
from app.models.enums import FindingEvidenceStatus
from app.services.screening.rules import MarketingRuleSet

INSTITUTION_REPORT_VERSION = "institution_compliance_report_v1"
CONSUMER_NOTICE_VERSION = "consumer_protection_notice_v1"
DISCLAIMER = (
    "本结果仅为确定性风险筛查与证据辅助，不构成违法认定或最终法律意见，"
    "需由合规人员结合完整材料复核。"
)


class ScreeningReportService:
    def __init__(self, ruleset: MarketingRuleSet) -> None:
        self.ruleset = ruleset
        self.rules = {rule.rule_id: rule for rule in ruleset.rules}

    def run_detail(self, session: Session, run_id: int) -> dict[str, Any]:
        run, material, findings = self._load(session, run_id)
        return {
            "material_id": material.id,
            "screening_run_id": run.id,
            "status": run.status,
            "ruleset_version": run.ruleset_version,
            "ruleset_sha256": run.ruleset_sha256,
            "trusted_index_payload_hash": run.trusted_index_payload_hash,
            "finding_count": run.finding_count,
            "insufficient_evidence_count": run.insufficient_evidence_count,
            "run_payload_sha256": run.run_payload_sha256,
            "findings": [self._finding_row(finding) for finding in findings],
        }

    def institution_report(self, session: Session, run_id: int) -> dict[str, Any]:
        run, material, findings = self._load(session, run_id)
        counts = Counter(finding.severity for finding in findings)
        evidence_insufficient = sum(
            finding.evidence_status == FindingEvidenceStatus.EVIDENCE_INSUFFICIENT.value
            for finding in findings
        )
        rows = []
        for finding in findings:
            row = self._finding_row(finding)
            row["surrounding_context"] = material.raw_text[
                max(0, finding.raw_start_offset - 40) : min(
                    len(material.raw_text), finding.raw_end_offset + 40
                )
            ]
            row["remediation_template"] = self.rules[
                finding.rule_id
            ].institution_remediation_template
            rows.append(row)
        return {
            "report_version": INSTITUTION_REPORT_VERSION,
            "material_id": material.id,
            "material_title": material.title,
            "material_sha256": material.input_sha256,
            "screening_run_id": run.id,
            "ruleset_version": run.ruleset_version,
            "trusted_index_payload_hash": run.trusted_index_payload_hash,
            "summary": {
                "finding_count": len(findings),
                "high_count": counts["high"],
                "medium_count": counts["medium"],
                "low_count": counts["low"],
                "evidence_insufficient_count": evidence_insufficient,
                "matched_rule_ids": sorted({finding.rule_id for finding in findings}),
            },
            "findings": rows,
            "evidence_summary": {
                "link_count": sum(len(finding.evidence_links) for finding in findings),
                "source_document_count": len(
                    {
                        link.source_document_snapshot_json["source_document_id"]
                        for finding in findings
                        for link in finding.evidence_links
                    }
                ),
            },
            "manual_review_required": bool(findings),
            "disclaimer": DISCLAIMER,
        }

    def consumer_notice(self, session: Session, run_id: int) -> dict[str, Any]:
        _, material, findings = self._load(session, run_id)
        evidence_links = []
        seen: set[str] = set()
        for finding in findings:
            for link in finding.evidence_links:
                source_url = str(link.source_document_snapshot_json["source_url"])
                key = f"{source_url}:{link.chunk_identity_sha256}"
                if key not in seen:
                    seen.add(key)
                    evidence_links.append(
                        {
                            "source_url": source_url,
                            "title": link.source_document_snapshot_json["title"],
                            "support_type": link.support_type,
                            "chunk_identity_sha256": link.chunk_identity_sha256,
                        }
                    )
        return {
            "notice_version": CONSUMER_NOTICE_VERSION,
            "material_title": material.title,
            "risk_prompts": sorted(
                {self.rules[finding.rule_id].consumer_notice_template for finding in findings}
            ),
            "questions_to_ask": sorted({finding.review_question for finding in findings}),
            "evidence_links": evidence_links,
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _finding_row(finding: RiskFinding) -> dict[str, Any]:
        return {
            "finding_sha256": finding.finding_sha256,
            "rule_id": finding.rule_id,
            "category": finding.category,
            "severity": finding.severity,
            "signal_strength": finding.signal_strength,
            "matched_text": finding.matched_text,
            "raw_start_offset": finding.raw_start_offset,
            "raw_end_offset": finding.raw_end_offset,
            "explanation": finding.explanation,
            "review_question": finding.review_question,
            "evidence_status": finding.evidence_status,
            "evidence": [
                {
                    "support_type": link.support_type,
                    "retrieval_rank": link.retrieval_rank,
                    "retrieval_score": link.retrieval_score,
                    "chunk_identity_sha256": link.chunk_identity_sha256,
                    "chunk_content_sha256": link.chunk_content_sha256,
                    "source": link.source_document_snapshot_json,
                    "source_locator": link.source_locator_snapshot_json,
                    "evidence_references": link.evidence_references_snapshot_json,
                }
                for link in sorted(
                    finding.evidence_links,
                    key=lambda value: (value.support_type, value.retrieval_rank, value.id),
                )
            ],
        }

    @staticmethod
    def _load(
        session: Session, run_id: int
    ) -> tuple[ScreeningRun, MarketingMaterial, list[RiskFinding]]:
        run = session.scalar(
            select(ScreeningRun)
            .options(selectinload(ScreeningRun.findings).selectinload(RiskFinding.evidence_links))
            .where(ScreeningRun.id == run_id)
        )
        if run is None or run.status != "completed":
            raise ScreeningError("screening_run_not_found")
        findings = sorted(
            run.findings,
            key=lambda value: (
                value.raw_start_offset,
                value.raw_end_offset,
                value.rule_id,
                value.finding_sha256,
            ),
        )
        return run, run.material, findings
