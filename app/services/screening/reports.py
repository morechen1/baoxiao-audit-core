from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ScreeningError
from app.models import MarketingMaterial, RiskFinding, ScreeningRun
from app.models.enums import FindingEvidenceStatus
from app.services.screening.evidence import evaluate_semantic_support
from app.services.screening.rules import EvidenceMatcher

INSTITUTION_REPORT_VERSION = "institution_compliance_report_v1"
CONSUMER_NOTICE_VERSION = "consumer_protection_notice_v1"
DISCLAIMER = (
    "本结果仅为确定性风险筛查与证据辅助，不构成违法认定或最终法律意见，"
    "需由合规人员结合完整材料复核。"
)
ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE = (
    "该条款仅用于展示同类保险合同中可能存在的等待期、现金价值、退保损失或"
    "责任免除结构，不代表输入材料对应的具体产品条款。"
)
SUPPORT_TYPE_PRIORITY = {
    "normative_basis": 0,
    "enforcement_example": 1,
    "product_term_context": 2,
}
CANONICAL_REPORT_EXCLUDED_KEYS = {
    "material_id",
    "screening_run_id",
    "source_document_id",
}


class ScreeningReportService:
    def run_detail(self, session: Session, run_id: int) -> dict[str, Any]:
        run, material, findings = self._load(session, run_id)
        return {
            "material_id": material.id,
            "screening_run_id": run.id,
            "status": run.status,
            "ruleset_version": run.ruleset_version,
            "ruleset_sha256": run.ruleset_sha256,
            "ruleset_snapshot_sha256": run.ruleset_snapshot_sha256,
            "trusted_index_payload_hash": run.trusted_index_payload_hash,
            "finding_count": run.finding_count,
            "insufficient_evidence_count": run.insufficient_evidence_count,
            "run_payload_sha256": run.run_payload_sha256,
            "evidence_evaluation_summary": run.evidence_evaluation_summary_json,
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
            row["remediation_template"] = finding.remediation_template
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
                "selected_evidence_links": sum(len(finding.evidence_links) for finding in findings),
                "selected_evidence_links_semantically_valid": sum(
                    _link_semantically_valid(finding, link)
                    for finding in findings
                    for link in finding.evidence_links
                ),
                "selected_irrelevant_links": sum(
                    not _link_semantically_valid(finding, link)
                    for finding in findings
                    for link in finding.evidence_links
                ),
                "source_document_count": len(
                    {
                        link.source_document_snapshot_json["source_document_id"]
                        for finding in findings
                        for link in finding.evidence_links
                    }
                ),
            },
            "product_context_notice": (
                ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE
                if _has_illustrative_product_context(findings)
                else None
            ),
            "manual_review_required": bool(findings),
            "disclaimer": DISCLAIMER,
        }

    def consumer_notice(self, session: Session, run_id: int) -> dict[str, Any]:
        _, material, findings = self._load(session, run_id)
        evidence_links = []
        seen: set[str] = set()
        for finding in findings:
            for link in _sorted_evidence_links(finding):
                source_url = str(link.source_document_snapshot_json["source_url"])
                key = f"{source_url}:{link.chunk_identity_sha256}"
                if key not in seen:
                    seen.add(key)
                    evidence_links.append(
                        {
                            "source_url": source_url,
                            "title": link.source_document_snapshot_json["title"],
                            "support_type": link.support_type,
                            "context_scope": link.context_scope,
                            "chunk_identity_sha256": link.chunk_identity_sha256,
                            "actual_matched_substrings": link.actual_matched_substrings,
                            "matched_pattern_groups": link.matched_pattern_groups,
                            "semantic_support_score": link.semantic_support_score,
                            "semantic_support_reason": link.semantic_support_reason,
                        }
                    )
        return {
            "notice_version": CONSUMER_NOTICE_VERSION,
            "material_title": material.title,
            "risk_prompts": sorted({finding.consumer_notice_template for finding in findings}),
            "questions_to_ask": sorted({finding.review_question for finding in findings}),
            "evidence_links": evidence_links,
            "product_context_notice": (
                ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE
                if _has_illustrative_product_context(findings)
                else None
            ),
            "disclaimer": DISCLAIMER,
        }

    @staticmethod
    def _finding_row(finding: RiskFinding) -> dict[str, Any]:
        return {
            "finding_sha256": finding.finding_sha256,
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "rule_snapshot_sha256": finding.rule_snapshot_sha256,
            "category": finding.category,
            "severity": finding.severity,
            "signal_strength": finding.signal_strength,
            "matched_text": finding.matched_text,
            "raw_start_offset": finding.raw_start_offset,
            "raw_end_offset": finding.raw_end_offset,
            "explanation": finding.explanation,
            "review_question": finding.review_question,
            "rule_snapshot": finding.rule_snapshot_json,
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
                    "support_evaluation_version": link.support_evaluation_version,
                    "support_evaluation_passed": link.support_evaluation_passed,
                    "matched_support_patterns": link.matched_support_patterns,
                    "actual_matched_substrings": link.actual_matched_substrings,
                    "matched_pattern_groups": link.matched_pattern_groups,
                    "matched_evidence_fields": link.matched_evidence_fields,
                    "support_reason": link.support_reason,
                    "semantic_support_score": link.semantic_support_score,
                    "semantic_support_reason": link.semantic_support_reason,
                    "context_scope": link.context_scope,
                }
                for link in _sorted_evidence_links(finding)
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


def _has_illustrative_product_context(findings: list[RiskFinding]) -> bool:
    return any(
        link.context_scope == "illustrative_not_material_specific"
        for finding in findings
        for link in finding.evidence_links
    )


def _sorted_evidence_links(finding: RiskFinding) -> list[Any]:
    return sorted(
        finding.evidence_links,
        key=lambda value: (
            SUPPORT_TYPE_PRIORITY[value.support_type],
            value.retrieval_rank,
            -value.retrieval_score,
            str(value.source_document_snapshot_json.get("pilot_id") or ""),
            value.chunk_identity_sha256,
        ),
    )


def _link_semantically_valid(finding: RiskFinding, link: Any) -> bool:
    matchers = finding.rule_snapshot_json.get("evidence_matchers")
    if not isinstance(matchers, dict):
        return False
    raw_matcher = matchers.get(link.support_type)
    if not isinstance(raw_matcher, dict):
        return False
    try:
        matcher = EvidenceMatcher.model_validate(raw_matcher)
    except ValueError:
        return False
    if link.source_document_snapshot_json.get("chunk_kind") not in matcher.allowed_chunk_kinds:
        return False
    references = [
        item
        for item in link.evidence_references_snapshot_json
        if str(item.get("field_name") or "") in matcher.required_evidence_fields
    ]
    fields = sorted({str(item.get("field_name") or "") for item in references})
    if not fields:
        return False
    semantic = evaluate_semantic_support(
        matcher,
        "\n".join(str(item.get("quote") or "") for item in references),
    )
    return bool(
        semantic["passed"]
        and link.support_evaluation_passed
        and list(semantic["actual_matched_substrings"]) == link.actual_matched_substrings
        and list(semantic["matched_pattern_groups"]) == link.matched_pattern_groups
        and abs(float(semantic["score"]) - link.semantic_support_score) < 1e-12
        and str(semantic["reason"]) == link.semantic_support_reason
    )


def canonical_report_payload(report: dict[str, Any]) -> dict[str, Any]:
    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: normalize(item)
                for key, item in sorted(value.items())
                if key not in CANONICAL_REPORT_EXCLUDED_KEYS
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return cast(dict[str, Any], normalize(report))


def canonical_report_sha256(report: dict[str, Any]) -> str:
    payload = json.dumps(
        canonical_report_payload(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
