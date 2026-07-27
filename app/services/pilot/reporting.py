from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Penalty,
    PilotCollectionItem,
    PilotCollectionRun,
    ProductDocument,
    Regulation,
    RegulatoryCase,
    SourceDocument,
)
from app.models.enums import KnowledgeIndexStatus, ReviewStatus
from app.services.field_evidence import EVIDENCE_FIELDS
from app.services.pilot.models import PilotSourceType

REVIEWED_STATUSES = frozenset(
    {
        ReviewStatus.APPROVED.value,
        ReviewStatus.APPROVED_WITH_REVISION.value,
        ReviewStatus.REJECTED.value,
        ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
        ReviewStatus.REJECTED_HALLUCINATION.value,
        ReviewStatus.REJECTED_DUPLICATE.value,
        ReviewStatus.REJECTED_OUTDATED.value,
        ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
    }
)


class PilotReportService:
    def status(
        self,
        session: Session,
        *,
        run_id: int | None = None,
        scope: str = "cumulative",
    ) -> dict[str, Any]:
        runs, items = self._ledger_scope(session, run_id=run_id, scope=scope)
        totals = self._metrics(session, runs, items)
        by_type = {
            source_type.value: self._metrics(
                session,
                [],
                [item for item in items if item.source_type == source_type.value],
                planned_override=sum(item.source_type == source_type.value for item in items),
            )
            for source_type in PilotSourceType
        }
        return {
            "scope": "run" if run_id is not None else scope,
            "run_id": run_id,
            "totals": totals,
            "by_type": by_type,
        }

    def quality_report(
        self,
        session: Session,
        *,
        run_id: int | None = None,
        scope: str = "cumulative",
    ) -> dict[str, Any]:
        runs, items = self._ledger_scope(session, run_id=run_id, scope=scope)
        metrics = self._metrics(session, runs, items)
        structured_records = [
            (document, record)
            for document in self._documents(session, items)
            for record in self._structured_records(session, document)
        ]
        nonempty_fields = evidence_backed_fields = total_evidence_fields = 0
        for document, record in structured_records:
            fields = EVIDENCE_FIELDS.get(document.data_type, frozenset())
            evidence = record.field_evidence_json
            for field_name in fields:
                value = getattr(record, field_name)
                if value is not None and (not isinstance(value, str) or value.strip()):
                    nonempty_fields += 1
                    evidence_backed_fields += int(bool(evidence.get(field_name)))
                total_evidence_fields += 1
        errors_by_source: dict[str, Counter[str]] = {}
        for item in items:
            if not item.last_error_code:
                continue
            errors_by_source.setdefault(item.source_key, Counter())[item.last_error_code] += 1
        report = {
            **metrics,
            "scope": "run" if run_id is not None else scope,
            "run_id": run_id,
            "source_count": len({item.source_registration_id for item in items}),
            "download_success_rate": self._ratio(
                metrics["collected_count"], metrics["attempted_count"]
            ),
            "duplicate_rate": self._ratio(
                metrics["duplicate_content_count"], metrics["collected_count"]
            ),
            "parse_success_rate": self._ratio(metrics["parsed_count"], metrics["collected_count"]),
            "scanned_pdf_rate": self._ratio(
                metrics["requires_ocr_count"], metrics["collected_count"]
            ),
            "field_completeness_rate": self._ratio(nonempty_fields, total_evidence_fields),
            "field_evidence_coverage_rate": self._ratio(evidence_backed_fields, nonempty_fields),
            "automatic_validation_pass_rate": self._ratio(
                metrics["auto_validation_pass_count"],
                metrics["structured_count"],
            ),
            "errors_by_source": {
                key: dict(sorted(value.items())) for key, value in sorted(errors_by_source.items())
            },
            "status_by_type": self.status(
                session,
                run_id=run_id,
                scope=scope,
            )["by_type"],
        }
        self._assert_portable(report)
        return report

    def write_quality_report(
        self,
        session: Session,
        output: Path,
        *,
        run_id: int | None = None,
        scope: str = "cumulative",
    ) -> tuple[Path, Path]:
        report = self.quality_report(session, run_id=run_id, scope=scope)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path = output.with_suffix(".md")
        markdown_path.write_text(self._markdown(report), encoding="utf-8")
        return output, markdown_path

    @staticmethod
    def _ledger_scope(
        session: Session,
        *,
        run_id: int | None,
        scope: str,
    ) -> tuple[list[PilotCollectionRun], list[PilotCollectionItem]]:
        if scope != "cumulative" and run_id is None:
            raise ValueError("scope must be cumulative unless run_id is provided")
        run_statement = select(PilotCollectionRun)
        item_statement = select(PilotCollectionItem)
        if run_id is not None:
            run_statement = run_statement.where(PilotCollectionRun.id == run_id)
            item_statement = item_statement.where(PilotCollectionItem.run_id == run_id)
        runs = list(session.scalars(run_statement))
        if run_id is not None and not runs:
            raise ValueError("pilot_run_not_found")
        return runs, list(session.scalars(item_statement))

    def _metrics(
        self,
        session: Session,
        runs: list[PilotCollectionRun],
        items: list[PilotCollectionItem],
        *,
        planned_override: int | None = None,
    ) -> dict[str, int]:
        documents = self._documents(session, items)
        structured_count = sum(
            bool(self._structured_records(session, document)) for document in documents
        )
        return {
            "planned_count": (
                planned_override
                if planned_override is not None
                else sum(run.planned_count for run in runs)
            ),
            "attempted_count": sum(item.attempt_count > 0 for item in items),
            "collected_count": sum(item.status == "collected" for item in items),
            "failed_count": sum(
                item.status in {"failed", "blocked_not_implemented"} for item in items
            ),
            "skipped_count": sum(run.skipped_count for run in runs),
            "unique_document_count": len(
                {item.document_id for item in items if item.document_id is not None}
            ),
            "duplicate_content_count": sum(
                item.status == "collected" and item.document_created is False for item in items
            ),
            "parsed_count": sum(document.parse_status == "parsed" for document in documents),
            "requires_ocr_count": sum(
                document.parse_status == "requires_ocr" for document in documents
            ),
            "structured_count": structured_count,
            "auto_validation_pass_count": sum(
                document.metadata_json.get("automatic_validation", {}).get("valid") is True
                for document in documents
            ),
            "pending_review_count": sum(
                document.final_review_status == ReviewStatus.PENDING_REVIEW.value
                for document in documents
            ),
            "reviewed_count": sum(
                document.final_review_status in REVIEWED_STATUSES for document in documents
            ),
            "indexed_count": sum(
                document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
                for document in documents
            ),
        }

    @staticmethod
    def _documents(session: Session, items: list[PilotCollectionItem]) -> list[SourceDocument]:
        document_ids = {item.document_id for item in items if item.document_id is not None}
        return [
            document
            for document_id in document_ids
            if (document := session.get(SourceDocument, document_id)) is not None
        ]

    @staticmethod
    def _structured_records(session: Session, document: SourceDocument) -> list[Any]:
        model: Any = {
            PilotSourceType.REGULATION.value: Regulation,
            PilotSourceType.PENALTY.value: Penalty,
            PilotSourceType.PRODUCT_DOCUMENT.value: ProductDocument,
            PilotSourceType.REGULATORY_CASE.value: RegulatoryCase,
        }.get(document.data_type)
        return (
            list(session.scalars(select(model).where(model.document_id == document.id)))
            if model
            else []
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    @staticmethod
    def _assert_portable(value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        forbidden = (
            "/Users/",
            "/home/",
            "\\Users\\",
            "file://",
            "Authorization:",
            "Bearer ",
        )
        if any(token in encoded for token in forbidden):
            raise ValueError("quality_report_contains_sensitive_value")

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        labels = [
            ("计划数量", "planned_count"),
            ("尝试数量", "attempted_count"),
            ("采集成功", "collected_count"),
            ("失败数量", "failed_count"),
            ("跳过数量", "skipped_count"),
            ("唯一文档", "unique_document_count"),
            ("重复内容", "duplicate_content_count"),
            ("解析成功", "parsed_count"),
            ("需要 OCR", "requires_ocr_count"),
            ("结构化完成", "structured_count"),
            ("自动校验通过", "auto_validation_pass_count"),
            ("待人工审核", "pending_review_count"),
            ("已审核", "reviewed_count"),
            ("已索引", "indexed_count"),
        ]
        rows = "\n".join(f"| {label} | {report[key]} |" for label, key in labels)
        return (
            "# Pilot 真实数据质量报告\n\n"
            "| 指标 | 值 |\n"
            "| --- | ---: |\n"
            f"{rows}\n\n"
            "事实来源为不可变 Pilot 数据库账本；报告不读取历史 JSONL，"
            "也不包含本地路径或异常原文。\n"
        )
