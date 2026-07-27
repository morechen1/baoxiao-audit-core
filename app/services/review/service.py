from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import Workbook
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    InvalidStateTransition,
    ReviewDecisionError,
)
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    EvaluationSample,
    ReviewBatch,
    ReviewBatchItem,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import (
    APPROVABLE_STATUSES,
    AuthenticityType,
    DatasetSplit,
    DataType,
    ReviewStatus,
)
from app.repositories import DocumentRepository
from app.schemas.review import EvaluationSampleRevision, StructuredRecordCorrections
from app.services.collection import SafeUrlPolicy
from app.services.state_machine import StateMachineService
from app.services.validation import ValidationService

SCHEMA_VERSION = "2.0"
LEGAL_REVIEW_STATUSES = ALLOWED_HUMAN_DECISIONS = frozenset(
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

CORRECTION_FIELDS: dict[str, frozenset[str]] = {
    DataType.REGULATION.value: frozenset(
        {
            "title",
            "document_number",
            "issuing_authority",
            "effective_date",
            "expiry_date",
            "validity_status",
            "article_number",
            "article_text",
        }
    ),
    DataType.PENALTY.value: frozenset(
        {
            "punished_entity",
            "authority",
            "document_number",
            "decision_date",
            "illegal_facts",
            "legal_basis",
            "penalty_result",
            "original_sales_wording_disclosed",
            "original_sales_wording",
        }
    ),
    DataType.PRODUCT_DOCUMENT.value: frozenset(
        {
            "company_name",
            "product_name",
            "product_type",
            "waiting_period",
            "cooling_off_period",
            "insurance_responsibility",
            "exclusions",
            "cash_value_description",
            "guaranteed_benefit",
            "non_guaranteed_benefit",
            "surrender_risk",
        }
    ),
    DataType.EVALUATION_SAMPLE.value: frozenset(
        {
            "sample_text",
            "sample_category",
            "risk_labels",
            "expected_evidence",
            "construction_basis",
            "split",
        }
    ),
}

PROTECTED_CORRECTION_FIELDS = frozenset(
    {
        "id",
        "document_id",
        "authenticity_type",
        "raw_text",
        "raw_file_path",
        "sha256",
        "source_quote",
        "final_review_status",
        "knowledge_index_status",
        "indexed_at",
        "created_at",
    }
)


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class ReviewService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def export_batch(self, session: Session, data_type: str, file_format: str) -> ReviewBatch:
        if file_format not in {"jsonl", "xlsx"}:
            raise ValueError("format must be jsonl or xlsx")
        records = self._pending_records(session, data_type)
        unique_name = f"{data_type}-{uuid4().hex}"
        output_dir = self.settings.data_dir.resolve() / "review_batches"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{unique_name}.{file_format}"
        batch = ReviewBatch(
            batch_name=unique_name,
            data_type=data_type,
            record_count=len(records),
            export_path=str(path),
            schema_version=SCHEMA_VERSION,
            status="exported",
        )
        session.add(batch)
        session.flush()
        rows: list[dict[str, Any]] = []
        for record in records:
            document = record if isinstance(record, SourceDocument) else None
            item = ReviewBatchItem(
                batch_id=batch.id,
                record_type=data_type,
                record_id=record.id,
                document_id=document.id if document else None,
                exported_status=record.final_review_status,
                payload_hash="0" * 64,
            )
            session.add(item)
            session.flush()
            row = (
                self._document_review_row(session, document, batch.id, item.id)
                if document
                else self._evaluation_review_row(record, batch.id, item.id)
            )
            item.payload_hash = payload_hash(row)
            rows.append(row)
        self._write_export(path, file_format, rows)
        batch.export_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        session.commit()
        session.refresh(batch)
        return batch

    def import_results(
        self, session: Session, path: Path, batch_id: int | None = None
    ) -> tuple[int, list[str]]:
        batch = session.get(ReviewBatch, batch_id) if batch_id is not None else None
        if batch is None:
            raise ReviewDecisionError("A valid batch_id is required")
        self._verify_review_package(batch)
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise ReviewDecisionError("Review result must be an existing JSONL file")
        imported = 0
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                self._apply_decision(session, payload, batch_id, commit=False)
                imported += 1
            except (
                json.JSONDecodeError,
                ReviewDecisionError,
                InvalidStateTransition,
                ValueError,
                TypeError,
            ) as exc:
                session.rollback()
                return 0, [f"line {line_number}: {exc}"]
        try:
            self._verify_review_package(batch)
        except ReviewDecisionError:
            session.rollback()
            raise
        session.commit()
        return imported, []

    def create_result_template(self, session: Session, batch_id: int) -> Path:
        batch = session.get(ReviewBatch, batch_id)
        if not batch:
            raise ReviewDecisionError("Review batch does not exist")
        self._verify_review_package(batch)
        items = list(
            session.scalars(
                select(ReviewBatchItem)
                .where(ReviewBatchItem.batch_id == batch.id)
                .order_by(ReviewBatchItem.id)
            )
        )
        output_dir = self.settings.data_dir.resolve() / "review_results"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{batch.batch_name}-results.jsonl"
        rows: list[dict[str, Any]] = [
            {
                "batch_id": batch.id,
                "batch_item_id": item.id,
                "reviewed_payload_hash": item.payload_hash,
                "schema_version": batch.schema_version,
                "record_id": item.record_id,
                "record_type": item.record_type,
                "final_status": "",
                "field_reviews": {},
                "corrections": {},
                "authenticity_decision": None,
                "evidence_quality": "",
                "review_comment": "",
                "reviewer": "",
            }
            for item in items
        ]
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def apply_decision(
        self,
        session: Session,
        payload: dict[str, Any],
        batch_id: int | None = None,
    ) -> ReviewDecision:
        try:
            return self._apply_decision(session, payload, batch_id, commit=True)
        except Exception:
            session.rollback()
            raise

    def _apply_decision(
        self,
        session: Session,
        payload: dict[str, Any],
        batch_id: int | None,
        *,
        commit: bool,
    ) -> ReviewDecision:
        required = {
            "batch_id",
            "batch_item_id",
            "reviewed_payload_hash",
            "schema_version",
            "record_id",
            "record_type",
            "final_status",
            "reviewer",
            "evidence_quality",
        }
        missing = sorted(required - payload.keys())
        if missing:
            raise ReviewDecisionError(f"Missing fields: {', '.join(missing)}")
        if batch_id is None:
            raise ReviewDecisionError("A valid batch_id is required")
        batch = session.get(ReviewBatch, batch_id)
        if not batch:
            raise ReviewDecisionError("Review batch does not exist")
        self._verify_review_package(batch)
        if payload["batch_id"] != batch_id:
            raise ReviewDecisionError("Payload batch_id does not match requested batch")
        item = session.get(ReviewBatchItem, int(payload["batch_item_id"]))
        if not item or item.batch_id != batch_id:
            raise ReviewDecisionError("Record is not part of this review batch")
        if item.decision_id is not None:
            raise ReviewDecisionError("Review batch item already has a decision")
        if payload["schema_version"] != batch.schema_version:
            raise ReviewDecisionError("Review decision schema_version does not match batch")
        if payload["reviewed_payload_hash"] != item.payload_hash:
            raise ReviewDecisionError("reviewed_payload_hash does not match review batch item")
        record_id = int(payload["record_id"])
        record_type = str(payload["record_type"])
        if item.record_id != record_id or item.record_type != record_type:
            raise ReviewDecisionError("Decision record does not match review batch item")
        final_status = str(payload["final_status"])
        if final_status not in ALLOWED_HUMAN_DECISIONS:
            raise ReviewDecisionError(f"Illegal final_status: {final_status}")
        quality = str(payload["evidence_quality"])
        if quality not in {"A", "B", "C", "D"}:
            raise ReviewDecisionError("evidence_quality must be A, B, C, or D")
        raw_corrections = payload.get("corrections", {})
        if raw_corrections is None:
            raw_corrections = {}
        if not isinstance(raw_corrections, dict):
            raise ReviewDecisionError("corrections must be an object")
        corrections = raw_corrections
        if final_status != ReviewStatus.APPROVED_WITH_REVISION.value and corrections:
            raise ReviewDecisionError(f"{final_status} decisions cannot contain corrections")
        if final_status == ReviewStatus.APPROVED_WITH_REVISION.value and not corrections:
            raise ReviewDecisionError("approved_with_revision requires corrections")
        reviewer_value = payload["reviewer"]
        if not isinstance(reviewer_value, str) or not reviewer_value.strip():
            raise ReviewDecisionError("reviewer must not be empty")
        reviewer = reviewer_value.strip()
        authenticity_decision = payload.get("authenticity_decision")
        if authenticity_decision not in {None, AuthenticityType.VERIFIED_PUBLIC.value}:
            raise ReviewDecisionError("Illegal authenticity_decision")
        if authenticity_decision and final_status not in APPROVABLE_STATUSES:
            raise ReviewDecisionError("Only approved decisions may verify public authenticity")
        if record_type == DataType.EVALUATION_SAMPLE.value:
            self._validate_evaluation_correction_keys(corrections)
            if authenticity_decision:
                raise ReviewDecisionError(
                    "Evaluation samples cannot receive public authenticity decisions"
                )
            return self._apply_evaluation_decision(
                session,
                batch,
                item,
                payload,
                final_status,
                corrections,
                quality,
                commit=commit,
            )
        correction_set = self._parse_structured_corrections(record_type, corrections)
        document = DocumentRepository(session).get(record_id)
        if not document or document.data_type != record_type or item.document_id != document.id:
            raise ReviewDecisionError("Decision does not identify the exported document")
        if document.final_review_status != ReviewStatus.PENDING_REVIEW.value:
            raise ReviewDecisionError(
                f"Only pending_review records may be reviewed; "
                f"current={document.final_review_status}"
            )
        current_payload = self._document_review_row(session, document, batch.id, item.id)
        if payload_hash(current_payload) != item.payload_hash:
            raise ReviewDecisionError("Review payload changed after batch export")
        records = StateMachineService.structured_records(session, document)
        if not records:
            raise ReviewDecisionError("Structured record is missing")
        records_by_id = {record.id: record for record in records}
        for correction in correction_set.records if correction_set else []:
            target = records_by_id.get(correction.structured_record_id)
            if target is None:
                raise ReviewDecisionError(
                    "structured_record_id does not belong to the reviewed document"
                )
            for key, value in correction.fields.items():
                setattr(target, key, value)
        try:
            session.flush()
        except IntegrityError as exc:
            raise ReviewDecisionError("Corrections violate a database constraint") from exc
        validation = ValidationService().evaluate_document(session, document, records)
        if not validation.valid:
            codes = ",".join(issue.code for issue in validation.issues)
            raise ReviewDecisionError(f"Corrections failed deterministic validation: {codes}")
        if corrections:
            metadata = dict(document.metadata_json)
            metadata["post_review_validation"] = {
                "valid": validation.valid,
                "issues": [issue.__dict__ for issue in validation.issues],
                "validated_at": datetime.now(UTC).isoformat(),
                "reviewed_payload_hash": item.payload_hash,
            }
            document.metadata_json = metadata
            document.corrected_fields_json = {
                **document.corrected_fields_json,
                "records": corrections["records"],
            }
        StateMachineService.transition_document(session, document, final_status, "human review")
        decision = self._new_decision(
            batch.id, record_type, record_id, payload, final_status, corrections, quality
        )
        session.add(decision)
        try:
            session.flush()
        except IntegrityError as exc:
            raise ReviewDecisionError("Decision violates a database constraint") from exc
        item.decision_id = decision.id
        if authenticity_decision:
            self._verify_authenticity(session, document, decision, reviewer)
        self._complete_batch_if_ready(session, batch)
        if commit:
            session.commit()
            session.refresh(decision)
        else:
            session.flush()
        return decision

    def _apply_evaluation_decision(
        self,
        session: Session,
        batch: ReviewBatch,
        item: ReviewBatchItem,
        payload: dict[str, Any],
        final_status: str,
        corrections: dict[str, Any],
        quality: str,
        *,
        commit: bool,
    ) -> ReviewDecision:
        sample = session.get(EvaluationSample, item.record_id)
        if not sample:
            raise ReviewDecisionError("Evaluation sample does not exist")
        current_payload = self._evaluation_review_row(sample, batch.id, item.id)
        if payload_hash(current_payload) != item.payload_hash:
            raise ReviewDecisionError("Review payload changed after batch export")
        if sample.final_review_status != ReviewStatus.PENDING_REVIEW.value:
            raise ReviewDecisionError("Evaluation sample is not pending review")
        candidate = {
            "sample_text": sample.sample_text,
            "sample_category": sample.sample_category,
            "risk_labels": sample.risk_labels,
            "expected_evidence": sample.expected_evidence,
            "construction_basis": sample.construction_basis,
            "authenticity_type": sample.authenticity_type,
            "split": sample.split,
            **corrections,
        }
        try:
            revision = EvaluationSampleRevision.model_validate(candidate)
        except PydanticValidationError as exc:
            raise ReviewDecisionError(f"Invalid evaluation sample revision: {exc}") from exc
        if (
            sample.split == DatasetSplit.SEALED_TEST.value
            and revision.split.value != DatasetSplit.SEALED_TEST.value
        ):
            raise ReviewDecisionError("sealed_test samples cannot change split")
        for key, value in revision.model_dump(mode="json").items():
            setattr(sample, key, value)
        try:
            session.flush()
        except IntegrityError as exc:
            raise ReviewDecisionError(
                "Evaluation sample revision violates a database constraint"
            ) from exc
        StateMachineService.transition_evaluation_sample(
            session, sample, final_status, "human review"
        )
        decision = self._new_decision(
            batch.id,
            DataType.EVALUATION_SAMPLE.value,
            sample.id,
            payload,
            final_status,
            corrections,
            quality,
        )
        session.add(decision)
        session.flush()
        item.decision_id = decision.id
        self._complete_batch_if_ready(session, batch)
        if commit:
            session.commit()
            session.refresh(decision)
        else:
            session.flush()
        return decision

    @staticmethod
    def _new_decision(
        batch_id: int,
        record_type: str,
        record_id: int,
        payload: dict[str, Any],
        final_status: str,
        corrections: dict[str, Any],
        quality: str,
    ) -> ReviewDecision:
        return ReviewDecision(
            batch_id=batch_id,
            record_type=record_type,
            record_id=record_id,
            decision=final_status,
            field_reviews_json=payload.get("field_reviews") or {},
            corrections_json=corrections,
            evidence_quality=quality,
            review_comment=payload.get("review_comment"),
            reviewer=str(payload["reviewer"]).strip(),
            reviewed_payload_hash=str(payload["reviewed_payload_hash"]),
            schema_version=str(payload["schema_version"]),
        )

    @staticmethod
    def _validate_evaluation_correction_keys(corrections: dict[str, Any]) -> None:
        allowed = CORRECTION_FIELDS[DataType.EVALUATION_SAMPLE.value]
        for key in corrections:
            if key in PROTECTED_CORRECTION_FIELDS:
                raise ReviewDecisionError(f"Protected correction field: {key}")
            if key not in allowed:
                raise ReviewDecisionError(f"Unknown correction field: {key}")

    @staticmethod
    def _parse_structured_corrections(
        record_type: str, corrections: dict[str, Any]
    ) -> StructuredRecordCorrections | None:
        if not corrections:
            return None
        allowed = CORRECTION_FIELDS.get(record_type)
        if allowed is None:
            raise ReviewDecisionError(f"Unsupported record_type: {record_type}")
        try:
            correction_set = StructuredRecordCorrections.model_validate(corrections)
        except PydanticValidationError as exc:
            raise ReviewDecisionError(f"Invalid structured corrections: {exc}") from exc
        for correction in correction_set.records:
            for key in correction.fields:
                if key in PROTECTED_CORRECTION_FIELDS:
                    raise ReviewDecisionError(f"Protected correction field: {key}")
                if key not in allowed:
                    raise ReviewDecisionError(f"Unknown correction field: {key}")
        return correction_set

    @staticmethod
    def _verify_review_package(batch: ReviewBatch) -> None:
        path = Path(batch.export_path)
        if (
            not path.is_file()
            or not batch.export_sha256
            or hashlib.sha256(path.read_bytes()).hexdigest() != batch.export_sha256
        ):
            raise ReviewDecisionError("review_package_tampered")

    @staticmethod
    def _verify_authenticity(
        session: Session,
        document: SourceDocument,
        decision: ReviewDecision,
        reviewer: str,
    ) -> None:
        source = session.get(DataSource, document.source_id)
        if (
            not source
            or not source.enabled
            or source.source_type != document.data_type
            or not source.base_url
        ):
            raise ReviewDecisionError(
                "Authenticity verification requires an enabled registered source"
            )
        if document.authenticity_type != AuthenticityType.PENDING_VERIFICATION.value:
            raise ReviewDecisionError(
                "Only pending_verification documents may be verified as public"
            )
        allowed_domains = source.crawl_policy.get("allowed_domains", [])
        if not isinstance(allowed_domains, list) or any(
            not isinstance(value, str) for value in allowed_domains
        ):
            raise ReviewDecisionError("Registered source allowed_domains is invalid")
        allowed_hosts = SafeUrlPolicy.allowed_hosts(source.base_url, allowed_domains)
        urls = [document.source_url, document.final_url]
        if any(
            not url or not SafeUrlPolicy.host_allowed_for_hosts(url, allowed_hosts) for url in urls
        ):
            raise ReviewDecisionError("Document URL does not match the registered source allowlist")
        records = StateMachineService.structured_records(session, document)
        if not records or any(
            not getattr(record, "source_quote", None)
            or record.source_quote not in (document.raw_text or "")
            for record in records
        ):
            raise ReviewDecisionError("source_quote cannot be located in immutable raw text")
        previous = document.authenticity_type
        document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
        session.add(
            AuthenticityDecisionLog(
                document_id=document.id,
                previous_authenticity_type=previous,
                new_authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
                reviewer=reviewer,
                review_decision_id=decision.id,
                reason="Human reviewer verified registered public source",
            )
        )

    @staticmethod
    def _pending_records(session: Session, data_type: str) -> list[Any]:
        if data_type == DataType.EVALUATION_SAMPLE.value:
            return list(
                session.scalars(
                    select(EvaluationSample).where(
                        EvaluationSample.final_review_status == ReviewStatus.PENDING_REVIEW.value
                    )
                )
            )
        return DocumentRepository(session).list(
            status=ReviewStatus.PENDING_REVIEW.value, data_type=data_type
        )

    @staticmethod
    def _document_review_row(
        session: Session,
        document: SourceDocument,
        batch_id: int,
        batch_item_id: int,
    ) -> dict[str, Any]:
        records = StateMachineService.structured_records(session, document)
        parsed_records = [
            {
                ("structured_record_id" if key == "id" else key): value
                for key, value in vars(record).items()
                if not key.startswith("_") and key != "document_id"
            }
            for record in records
        ]
        return {
            "batch_id": batch_id,
            "batch_item_id": batch_item_id,
            "document_id": document.id,
            "record_id": document.id,
            "record_type": document.data_type,
            "source_url": document.source_url,
            "final_url": document.final_url,
            "source_title": document.source_title,
            "publisher": document.publisher,
            "published_at": document.published_at,
            "sha256": document.sha256,
            "raw_file_path": document.raw_file_path,
            "raw_text": document.raw_text,
            "raw_text_file": document.raw_file_path,
            "parsed_fields": {"records": parsed_records},
            "source_quotes": [
                getattr(record, "source_quote", None)
                for record in records
                if getattr(record, "source_quote", None)
            ],
            "automatic_validation": document.metadata_json.get("automatic_validation", {}),
            "parsing_warnings": document.metadata_json.get("parsing", {}).get("warnings", []),
            "authenticity_type": document.authenticity_type,
            "current_status": document.final_review_status,
        }

    @staticmethod
    def _evaluation_review_row(
        sample: EvaluationSample, batch_id: int, batch_item_id: int
    ) -> dict[str, Any]:
        return {
            "batch_id": batch_id,
            "batch_item_id": batch_item_id,
            "document_id": None,
            "record_id": sample.id,
            "record_type": DataType.EVALUATION_SAMPLE.value,
            "source_url": None,
            "final_url": None,
            "source_title": "人工构造评测样本",
            "publisher": None,
            "published_at": None,
            "sha256": None,
            "raw_file_path": None,
            "raw_text": sample.sample_text,
            "raw_text_file": None,
            "parsed_fields": {
                "sample_category": sample.sample_category,
                "risk_labels": sample.risk_labels,
                "expected_evidence": sample.expected_evidence,
                "construction_basis": sample.construction_basis,
                "split": sample.split,
            },
            "source_quotes": [],
            "automatic_validation": {"valid": True, "issues": []},
            "parsing_warnings": [],
            "authenticity_type": sample.authenticity_type,
            "current_status": sample.final_review_status,
        }

    @staticmethod
    def _write_export(path: Path, file_format: str, rows: list[dict[str, Any]]) -> None:
        if file_format == "jsonl":
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                encoding="utf-8",
            )
            return
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "review"
        headers = list(rows[0]) if rows else ["batch_id", "batch_item_id"]
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

    @staticmethod
    def _complete_batch_if_ready(session: Session, batch: ReviewBatch) -> None:
        remaining = session.scalar(
            select(func.count(ReviewBatchItem.id)).where(
                ReviewBatchItem.batch_id == batch.id,
                ReviewBatchItem.decision_id.is_(None),
            )
        )
        if remaining == 0:
            batch.status = "completed"
            batch.completed_at = datetime.now(UTC)


def is_approved(status: str) -> bool:
    return status in APPROVABLE_STATUSES
