from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import (
    EvaluationSample,
    Penalty,
    ProductDocument,
    Regulation,
    ReviewBatch,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import APPROVABLE_STATUSES, DataType, ReviewStatus
from app.repositories import DocumentRepository

LEGAL_REVIEW_STATUSES = {status.value for status in ReviewStatus} - {
    ReviewStatus.COLLECTED.value,
    ReviewStatus.PARSED.value,
    ReviewStatus.PENDING_REVIEW.value,
    ReviewStatus.AUTO_VALIDATION_FAILED.value,
    ReviewStatus.INDEXED.value,
}


class ReviewService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def export_batch(self, session: Session, data_type: str, file_format: str) -> ReviewBatch:
        if file_format not in {"jsonl", "xlsx"}:
            raise ValueError("format must be jsonl or xlsx")
        records: list[Any]
        if data_type == DataType.EVALUATION_SAMPLE.value:
            records = list(
                session.scalars(
                    select(EvaluationSample).where(
                        EvaluationSample.final_review_status == ReviewStatus.PENDING_REVIEW.value
                    )
                )
            )
        else:
            records = DocumentRepository(session).list(
                status=ReviewStatus.PENDING_REVIEW.value, data_type=data_type
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_dir = self.settings.data_dir.resolve() / "review_batches"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{data_type}-{timestamp}.{file_format}"
        rows = [
            self._evaluation_review_row(item)
            if isinstance(item, EvaluationSample)
            else self._review_row(item)
            for item in records
        ]
        if file_format == "jsonl":
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                encoding="utf-8",
            )
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "review"
            headers = list(rows[0]) if rows else ["record_id", "record_type"]
            sheet.append(headers)
            for row in rows:
                sheet.append(
                    [
                        json.dumps(row[key], ensure_ascii=False, default=str)
                        if isinstance(row[key], (dict, list))
                        else row[key]
                        for key in headers
                    ]
                )
            workbook.save(path)
        batch = ReviewBatch(
            batch_name=f"{data_type}-{timestamp}",
            data_type=data_type,
            record_count=len(rows),
            export_path=str(path),
            status="exported",
        )
        session.add(batch)
        session.commit()
        session.refresh(batch)
        return batch

    def import_results(
        self, session: Session, path: Path, batch_id: int | None = None
    ) -> tuple[int, list[str]]:
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise ValueError("Review result must be an existing JSONL file")
        imported = 0
        errors: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                self.apply_decision(session, json.loads(line), batch_id)
                imported += 1
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                session.rollback()
                errors.append(f"line {line_number}: {exc}")
        return imported, errors

    def apply_decision(
        self, session: Session, payload: dict[str, Any], batch_id: int | None = None
    ) -> ReviewDecision:
        required = {"record_id", "record_type", "final_status", "reviewer", "evidence_quality"}
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"Missing fields: {', '.join(missing)}")
        try:
            record_id = int(payload["record_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("record_id must be an integer") from exc
        final_status = str(payload["final_status"])
        if final_status not in LEGAL_REVIEW_STATUSES:
            raise ValueError(f"Illegal final_status: {final_status}")
        quality = str(payload["evidence_quality"])
        if quality not in {"A", "B", "C", "D"}:
            raise ValueError("evidence_quality must be A, B, C, or D")
        corrections = payload.get("corrections") or {}
        if final_status == ReviewStatus.APPROVED_WITH_REVISION.value and not corrections:
            raise ValueError("approved_with_revision requires corrections")
        if not isinstance(corrections, dict):
            raise TypeError("corrections must be an object")
        if payload["record_type"] == DataType.EVALUATION_SAMPLE.value:
            return self._apply_evaluation_decision(
                session, record_id, payload, final_status, corrections, quality, batch_id
            )
        document = DocumentRepository(session).get(record_id)
        if not document or document.data_type != payload["record_type"]:
            raise ValueError("record_id and record_type do not identify a document")
        document.corrected_fields_json = {**document.corrected_fields_json, **corrections}
        self._apply_structured_corrections(session, document, corrections)
        DocumentRepository(session).transition(document, final_status, "human review")
        decision = ReviewDecision(
            batch_id=batch_id,
            record_type=document.data_type,
            record_id=document.id,
            decision=final_status,
            field_reviews_json=payload.get("field_reviews") or {},
            corrections_json=corrections,
            evidence_quality=quality,
            review_comment=payload.get("review_comment"),
            reviewer=str(payload["reviewer"]),
        )
        session.add(decision)
        session.commit()
        session.refresh(decision)
        return decision

    @staticmethod
    def _review_row(document: SourceDocument) -> dict[str, Any]:
        return {
            "record_id": document.id,
            "record_type": document.data_type,
            "source_url": document.source_url,
            "source_title": document.source_title,
            "publisher": document.publisher,
            "published_at": document.published_at,
            "raw_text_summary": (document.raw_text or "")[:500],
            "parsed_fields": document.corrected_fields_json,
            "source_quotes": document.metadata_json.get("source_quotes", []),
            "automatic_validation": document.metadata_json.get("automatic_validation", {}),
            "current_status": document.final_review_status,
        }

    @staticmethod
    def _evaluation_review_row(sample: EvaluationSample) -> dict[str, Any]:
        return {
            "record_id": sample.id,
            "record_type": DataType.EVALUATION_SAMPLE.value,
            "source_url": None,
            "source_title": "人工构造评测样本",
            "publisher": None,
            "published_at": None,
            "raw_text_summary": sample.sample_text[:500],
            "parsed_fields": {
                "sample_category": sample.sample_category,
                "risk_labels": sample.risk_labels,
                "expected_evidence": sample.expected_evidence,
                "construction_basis": sample.construction_basis,
                "split": sample.split,
            },
            "source_quotes": [],
            "automatic_validation": {"valid": True, "issues": []},
            "current_status": sample.final_review_status,
        }

    @staticmethod
    def _apply_evaluation_decision(
        session: Session,
        record_id: int,
        payload: dict[str, Any],
        final_status: str,
        corrections: dict[str, Any],
        quality: str,
        batch_id: int | None,
    ) -> ReviewDecision:
        sample = session.get(EvaluationSample, record_id)
        if not sample:
            raise ValueError("record_id and record_type do not identify an evaluation sample")
        protected = {"id", "authenticity_type", "created_at"}
        for key, value in corrections.items():
            if key not in protected and hasattr(sample, key):
                setattr(sample, key, value)
        old_status = sample.final_review_status
        sample.final_review_status = final_status
        from app.models import StatusHistory

        session.add(
            StatusHistory(
                record_type=DataType.EVALUATION_SAMPLE.value,
                record_id=sample.id,
                from_status=old_status,
                to_status=final_status,
                reason="human review",
            )
        )
        decision = ReviewDecision(
            batch_id=batch_id,
            record_type=DataType.EVALUATION_SAMPLE.value,
            record_id=sample.id,
            decision=final_status,
            field_reviews_json=payload.get("field_reviews") or {},
            corrections_json=corrections,
            evidence_quality=quality,
            review_comment=payload.get("review_comment"),
            reviewer=str(payload["reviewer"]),
        )
        session.add(decision)
        session.commit()
        session.refresh(decision)
        return decision

    @staticmethod
    def _apply_structured_corrections(
        session: Session, document: SourceDocument, corrections: dict[str, Any]
    ) -> None:
        model: Any = {
            DataType.REGULATION.value: Regulation,
            DataType.PENALTY.value: Penalty,
            DataType.PRODUCT_DOCUMENT.value: ProductDocument,
        }.get(document.data_type)
        if not model:
            return
        record = session.scalar(select(model).where(model.document_id == document.id))
        if not record:
            return
        protected = {"id", "document_id", "source_quote"}
        for key, value in corrections.items():
            if key not in protected and hasattr(record, key):
                setattr(record, key, value)


def is_approved(status: str) -> bool:
    return status in APPROVABLE_STATUSES
