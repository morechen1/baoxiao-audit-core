"""Aggregate completed V1 database reports into document-level platform views."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, cast

from sqlalchemy.orm import Session

from app.core.exceptions import PlatformResultError
from app.services.platform.long_document import spans_are_duplicates
from app.services.platform.store import PlatformScreeningResult
from app.services.screening.reports import DISCLAIMER, ScreeningReportService


class PlatformReportService:
    def __init__(self) -> None:
        self.v1 = ScreeningReportService()

    def detail(self, session: Session, result: PlatformScreeningResult) -> dict[str, Any]:
        findings = self._findings(session, result)
        diagnostics = self._diagnostics(session, result)
        return {
            "platform_result_id": result.result_id,
            "screening_run_id": result.chunks[0].screening_run_id,
            "screening_run_ids": [chunk.screening_run_id for chunk in result.chunks],
            "status": "completed",
            "material": {
                "title": result.material.title,
                "material_type": result.material.material_type,
                "source_filename": result.material.source_filename,
                "source_format": result.material.source_format,
                "raw_text": result.material.raw_text,
            },
            "finding_count": len(findings),
            "insufficient_evidence_count": sum(
                row.get("evidence_status") == "evidence_insufficient" for row in findings
            ),
            "evidence_evaluation_summary": diagnostics,
            "findings": findings,
            "runtime": dict(result.runtime),
        }

    def institution(self, session: Session, result: PlatformScreeningResult) -> dict[str, Any]:
        findings = self._findings(session, result)
        counts = Counter(str(row["severity"]) for row in findings)
        source_ids = {
            evidence["source"].get("source_document_id")
            for row in findings
            for evidence in cast(list[dict[str, Any]], row.get("evidence", []))
            if evidence.get("source", {}).get("source_document_id") is not None
        }
        link_count = sum(len(cast(list[Any], row.get("evidence", []))) for row in findings)
        return {
            "report_version": "institution_compliance_report_v1_platform_aggregate",
            "platform_result_id": result.result_id,
            "material_title": result.material.title,
            "material_sha256": hashlib.sha256(result.material.raw_text.encode()).hexdigest(),
            "screening_run_ids": [chunk.screening_run_id for chunk in result.chunks],
            "summary": {
                "finding_count": len(findings),
                "high_count": counts["high"],
                "medium_count": counts["medium"],
                "low_count": counts["low"],
                "evidence_insufficient_count": sum(
                    row.get("evidence_status") == "evidence_insufficient" for row in findings
                ),
                "matched_rule_ids": sorted({str(row["rule_id"]) for row in findings}),
            },
            "findings": findings,
            "evidence_summary": {
                "link_count": link_count,
                "selected_evidence_links": link_count,
                "source_document_count": len(source_ids),
            },
            "manual_review_required": bool(findings),
            "disclaimer": DISCLAIMER,
        }

    def consumer(self, session: Session, result: PlatformScreeningResult) -> dict[str, Any]:
        prompts: set[str] = set()
        questions: set[str] = set()
        links: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in result.chunks:
            report = self.v1.consumer_notice(session, chunk.screening_run_id)
            prompts.update(cast(list[str], report.get("risk_prompts", [])))
            questions.update(cast(list[str], report.get("questions_to_ask", [])))
            for link in cast(list[dict[str, Any]], report.get("evidence_links", [])):
                key = f"{link.get('source_url')}:{link.get('chunk_identity_sha256')}"
                if key not in seen:
                    seen.add(key)
                    links.append(link)
        return {
            "notice_version": "consumer_protection_notice_v1_platform_aggregate",
            "platform_result_id": result.result_id,
            "material_title": result.material.title,
            "risk_prompts": sorted(prompts),
            "questions_to_ask": sorted(questions),
            "evidence_links": links,
            "disclaimer": DISCLAIMER,
        }

    def summary(self, session: Session, result: PlatformScreeningResult) -> dict[str, Any]:
        institution = self.institution(session, result)
        summary = cast(dict[str, Any], institution["summary"])
        risk_level = (
            "high"
            if summary["high_count"]
            else "medium"
            if summary["medium_count"]
            else "low"
        )
        return {
            "platform_result_id": result.result_id,
            "screening_run_id": result.chunks[0].screening_run_id,
            "screening_run_ids": [chunk.screening_run_id for chunk in result.chunks],
            "title": result.material.title,
            "material": result.material.source_filename,
            "source_type": result.material.source_format,
            "character_count": len(result.material.raw_text),
            "status": "completed",
            "risk_level": risk_level,
            "finding_count": summary["finding_count"],
            "primary_rules": summary["matched_rule_ids"],
            "evidence_link_count": institution["evidence_summary"]["link_count"],
            "runtime": dict(result.runtime),
            "report_endpoints": self.endpoints(result.result_id),
        }

    @staticmethod
    def endpoints(result_id: str) -> dict[str, str]:
        base = f"/api/platform/results/{result_id}"
        return {
            "detail": base,
            "institution": f"{base}/institution-report",
            "consumer": f"{base}/consumer-notice",
            "html": f"{base}/reports/html",
            "json": f"{base}/reports/json",
        }

    def _findings(
        self, session: Session, result: PlatformScreeningResult
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for chunk in result.chunks:
            report = self.v1.institution_report(session, chunk.screening_run_id)
            for source in cast(list[dict[str, Any]], report["findings"]):
                row = dict(source)
                row["raw_start_offset"] = int(source["raw_start_offset"]) + chunk.start_offset
                row["raw_end_offset"] = int(source["raw_end_offset"]) + chunk.start_offset
                row["source_screening_run_id"] = chunk.screening_run_id
                row["document_chunk_ordinal"] = chunk.ordinal
                start = cast(int, row["raw_start_offset"])
                end = cast(int, row["raw_end_offset"])
                if result.material.raw_text[start:end] != row["matched_text"]:
                    raise PlatformResultError("platform_offset_mapping_failed")
                if any(
                    spans_are_duplicates(
                        str(existing["rule_id"]),
                        int(existing["raw_start_offset"]),
                        int(existing["raw_end_offset"]),
                        str(row["rule_id"]),
                        start,
                        end,
                    )
                    for existing in candidates
                ):
                    continue
                candidates.append(row)
        candidates.sort(
            key=lambda row: (
                int(row["raw_start_offset"]),
                int(row["raw_end_offset"]),
                str(row["rule_id"]),
                str(row["finding_sha256"]),
            )
        )
        for ordinal, row in enumerate(candidates, start=1):
            row["finding_key"] = f"F{ordinal:03d}"
            start = int(row["raw_start_offset"])
            end = int(row["raw_end_offset"])
            row["surrounding_context"] = result.material.raw_text[
                max(0, start - 40) : min(len(result.material.raw_text), end + 40)
            ]
        return candidates

    def _diagnostics(
        self, session: Session, result: PlatformScreeningResult
    ) -> dict[str, Any]:
        chunk_diagnostics = []
        for chunk in result.chunks:
            detail = self.v1.run_detail(session, chunk.screening_run_id)
            chunk_diagnostics.append(detail["evidence_evaluation_summary"])
        statuses = cast(list[str], result.runtime.get("semantic_statuses", []))
        if not statuses or set(statuses) == {"disabled"}:
            semantic_status = "disabled"
        elif any(status in {"failed", "partial"} for status in statuses):
            semantic_status = "failed"
        else:
            semantic_status = "completed"
        return {
            "detection_baseline": "V1",
            "document_chunks": len(result.chunks),
            "parser_calls": result.runtime["parser_calls"],
            "cache_hits": result.runtime["cache_hits"],
            "latency_ms": result.runtime["latency_ms"],
            "provider_fail_closed": result.runtime["provider_fail_closed"],
            "semantic_parser": {
                "version": "semantic_claim_parser_v1",
                "status": semantic_status,
                "provider_calls": result.runtime["parser_calls"],
                "cache_hits": result.runtime["cache_hits"],
                "document_chunks": len(result.chunks),
                "failure_preserved_deterministic": result.runtime["provider_fail_closed"],
            },
            "chunk_diagnostics": chunk_diagnostics,
        }
