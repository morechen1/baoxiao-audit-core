from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from openpyxl import Workbook
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    FieldEvidenceError,
    InvalidStateTransition,
    ReviewDecisionError,
)
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    DocumentOccurrence,
    EvaluationSample,
    PilotCollectionItem,
    RegulatoryCase,
    ReviewBatch,
    ReviewBatchItem,
    ReviewDecision,
    ReviewReservation,
    SourceDocument,
)
from app.models.enums import (
    APPROVABLE_STATUSES,
    AuthenticityType,
    DatasetSplit,
    DataType,
    RegulatoryCaseUsage,
    ReviewStatus,
)
from app.repositories import DocumentRepository
from app.schemas.review import (
    AuthenticityDecisionInput,
    EvaluationSampleRevision,
    StructuredRecordCorrections,
)
from app.schemas.structured import (
    PenaltyDraft,
    ProductDocumentDraft,
    RegulationDraft,
    RegulatoryCaseRevision,
    StrictDraft,
)
from app.services.collection import SafeUrlPolicy
from app.services.field_evidence import EVIDENCE_FIELDS, FieldEvidenceService
from app.services.integrity import RawArtifactIntegrityService
from app.services.knowledge import KnowledgeIndexService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService
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
    DataType.REGULATORY_CASE.value: frozenset(
        {
            "case_title",
            "publisher",
            "published_at",
            "case_category",
            "scenario_text",
            "marketing_wording_disclosed",
            "marketing_wording",
            "case_facts",
            "regulatory_analysis",
            "consumer_advice",
            "case_usage",
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

CORRECTION_MODELS: dict[str, type[StrictDraft]] = {
    DataType.REGULATION.value: RegulationDraft,
    DataType.PENALTY.value: PenaltyDraft,
    DataType.PRODUCT_DOCUMENT.value: ProductDocumentDraft,
    DataType.REGULATORY_CASE.value: RegulatoryCaseRevision,
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
        if not records:
            raise ReviewDecisionError("no_records_available_for_review")
        unique_name = f"{data_type}-{uuid4().hex}"
        output_dir = self.settings.data_dir.resolve() / "review_batches"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{unique_name}.{file_format}"
        batch = ReviewBatch(
            batch_name=unique_name,
            data_type=data_type,
            record_count=0,
            export_path=str(path),
            schema_version=SCHEMA_VERSION,
            status="exported",
        )
        session.add(batch)
        session.flush()
        rows: list[dict[str, Any]] = []
        for record in records:
            document = record if isinstance(record, SourceDocument) else None
            if document:
                RawArtifactIntegrityService(self.settings).verify(document)
                ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
                for structured_record in StateMachineService.structured_records(session, document):
                    try:
                        FieldEvidenceService(self.settings).validate(
                            session,
                            document,
                            {
                                field_name: getattr(structured_record, field_name)
                                for field_name in EVIDENCE_FIELDS[document.data_type]
                            },
                            structured_record.field_evidence_json,
                        )
                    except FieldEvidenceError as exc:
                        raise ReviewDecisionError(str(exc)) from exc
            try:
                with session.begin_nested():
                    session.add(
                        ReviewReservation(
                            record_type=data_type,
                            record_id=record.id,
                            document_id=document.id if document else None,
                            batch_id=batch.id,
                            status="active",
                        )
                    )
                    session.flush()
            except IntegrityError:
                continue
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
        if not rows:
            session.rollback()
            raise ReviewDecisionError("no_records_available_for_review")
        batch.record_count = len(rows)
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
        rows = self._result_template_rows(batch, items)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def cancel_batch(
        self,
        session: Session,
        batch_id: int,
        reason: str,
    ) -> ReviewBatch:
        batch = session.get(ReviewBatch, batch_id)
        if not batch or batch.status != "exported":
            raise ReviewDecisionError("review_batch_not_cancellable")
        if not reason.strip():
            raise ReviewDecisionError("review_batch_cancellation_reason_required")
        reservations = list(
            session.scalars(
                select(ReviewReservation).where(
                    ReviewReservation.batch_id == batch.id,
                    ReviewReservation.status == "active",
                )
            )
        )
        now = datetime.now(UTC)
        for reservation in reservations:
            reservation.status = "released"
            reservation.released_at = now
            reservation.release_reason = f"batch_cancelled: {reason.strip()}"
        batch.status = "cancelled"
        batch.cancelled_at = now
        batch.cancellation_reason = reason.strip()
        session.commit()
        session.refresh(batch)
        return batch

    def export_bundle(self, session: Session, data_type: str) -> tuple[ReviewBatch, Path]:
        batch = self.export_batch(session, data_type, "jsonl")
        items = list(
            session.scalars(
                select(ReviewBatchItem)
                .where(ReviewBatchItem.batch_id == batch.id)
                .order_by(ReviewBatchItem.id)
            )
        )
        export_path = Path(batch.export_path)
        rows = [
            json.loads(line)
            for line in export_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        artifact_members: dict[str, bytes] = {}
        item_by_id = {item.id: item for item in items}
        manifest_items: list[dict[str, Any]] = []
        for row in rows:
            document = session.get(SourceDocument, int(row["document_id"]))
            if not document:
                raise ReviewDecisionError("Review bundle document is missing")
            RawArtifactIntegrityService(self.settings).verify(document)
            ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
            source_path = Path(document.raw_file_path)
            source_member = f"sources/{document.sha256}{source_path.suffix.lower()}"
            source_bytes = source_path.read_bytes()
            artifact_members.setdefault(source_member, source_bytes)
            parsed_member = f"parsed/{document.parsed_artifact_sha256}.json"
            parsed_bytes = Path(document.parsed_artifact_path or "").read_bytes()
            artifact_members.setdefault(parsed_member, parsed_bytes)
            row["raw_file_path"] = source_member
            row["raw_text_file"] = source_member
            row["parsed_artifact_path"] = parsed_member
            row["source_url"] = self._portable_url(row.get("source_url"))
            row["final_url"] = self._portable_url(row.get("final_url"))
            row["source_occurrences"] = self._portable_occurrences(row["source_occurrences"])
            item = item_by_id[int(row["batch_item_id"])]
            item.payload_hash = payload_hash(row)
            manifest_items.append(
                {
                    "batch_item_id": item.id,
                    "record_id": item.record_id,
                    "payload_hash": item.payload_hash,
                    "raw_sha256": document.sha256,
                    "raw_bundle_relative_path": source_member,
                    "parsed_artifact_sha256": document.parsed_artifact_sha256,
                    "parsed_text_sha256": document.parsed_text_sha256,
                    "parsed_bundle_relative_path": parsed_member,
                    "parser_name": document.parser_name,
                    "parser_version": document.parser_version,
                    "field_evidence_summary": [
                        FieldEvidenceService.summary(getattr(record, "field_evidence_json", {}))
                        for record in StateMachineService.structured_records(session, document)
                    ],
                    "source_occurrences": row["source_occurrences"],
                }
            )
        review_bytes = "".join(
            json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows
        ).encode()
        export_path.write_bytes(review_bytes)
        batch.export_sha256 = hashlib.sha256(review_bytes).hexdigest()
        template_rows = self._result_template_rows(batch, items)
        template_bytes = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in template_rows
        ).encode()
        package_payload_hash = self._bundle_payload_hash(
            review_bytes, template_bytes, artifact_members
        )
        manifest = {
            "schema_version": batch.schema_version,
            "batch_id": batch.id,
            "package_sha256": package_payload_hash,
            "integrity_scheme": "member-sha256+server-pinned-zip-sha256",
            "review_jsonl_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "result_template_sha256": hashlib.sha256(template_bytes).hexdigest(),
            "items": manifest_items,
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2, default=str, sort_keys=True
        ).encode()
        output_dir = self.settings.data_dir.resolve() / "review_bundles"
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = output_dir / f"{batch.batch_name}.zip"
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", manifest_bytes)
            bundle.writestr("review.jsonl", review_bytes)
            bundle.writestr("review-results-template.jsonl", template_bytes)
            for name, content in sorted(artifact_members.items()):
                bundle.writestr(name, content)
        batch.bundle_path = str(bundle_path)
        batch.bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        batch.bundle_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        session.commit()
        session.refresh(batch)
        return batch, bundle_path

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
        if batch.status != "exported":
            raise ReviewDecisionError("review_batch_not_open")
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
        raw_authenticity_decision = payload.get("authenticity_decision")
        try:
            authenticity_decision = (
                AuthenticityDecisionInput.model_validate(raw_authenticity_decision)
                if raw_authenticity_decision is not None
                else None
            )
        except PydanticValidationError as exc:
            raise ReviewDecisionError("invalid_authenticity_decision") from exc
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
        RawArtifactIntegrityService(self.settings).verify(document)
        ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        if document.final_review_status != ReviewStatus.PENDING_REVIEW.value:
            raise ReviewDecisionError(
                f"Only pending_review records may be reviewed; "
                f"current={document.final_review_status}"
            )
        if (
            document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
            and final_status in APPROVABLE_STATUSES
            and authenticity_decision is None
        ):
            raise ReviewDecisionError(
                "pending_verification approval requires authenticity_decision; "
                "use pending_source_verification"
            )
        current_payload = self._document_review_row(session, document, batch.id, item.id)
        if payload_hash(current_payload) != item.payload_hash:
            raise ReviewDecisionError("Review payload changed after batch export")
        records = StateMachineService.structured_records(session, document)
        if not records:
            raise ReviewDecisionError("Structured record is missing")
        records_by_id = {record.id: record for record in records}
        validated_updates: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        for correction in correction_set.records if correction_set else []:
            target = records_by_id.get(correction.structured_record_id)
            if target is None:
                raise ReviewDecisionError(
                    "structured_record_id does not belong to the reviewed document"
                )
            model = CORRECTION_MODELS[record_type]
            evidence_fields = EVIDENCE_FIELDS[record_type]
            candidate = {field: getattr(target, field) for field in model.model_fields}
            candidate.update(correction.fields)
            try:
                validated = model.model_validate(candidate).model_dump()
            except PydanticValidationError as exc:
                raise ReviewDecisionError("invalid_correction_value") from exc
            if (
                isinstance(target, RegulatoryCase)
                and target.case_usage == RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value
                and validated["case_usage"] != RegulatoryCaseUsage.SEALED_EXTERNAL_TEST.value
            ):
                raise ReviewDecisionError("sealed_external_test_cannot_be_reopened")
            changed_evidence_fields = {
                field
                for field in set(correction.fields) & evidence_fields
                if validated[field] is not None and str(validated[field]).strip()
            }
            if set(correction.field_evidence) != changed_evidence_fields:
                raise ReviewDecisionError("correction_evidence_required")
            candidate_evidence = {
                **target.field_evidence_json,
                **{
                    key: [item.model_dump(mode="json", exclude_none=True) for item in values]
                    for key, values in correction.field_evidence.items()
                },
            }
            for field in set(correction.fields) & evidence_fields:
                if validated[field] is None or not str(validated[field]).strip():
                    candidate_evidence.pop(field, None)
            try:
                validated_evidence = FieldEvidenceService(self.settings).validate(
                    session,
                    document,
                    validated,
                    candidate_evidence,
                )
            except (PydanticValidationError, FieldEvidenceError) as exc:
                raise ReviewDecisionError("invalid_correction_value") from exc
            validated_updates.append(
                (
                    target,
                    {key: validated[key] for key in correction.fields},
                    validated_evidence,
                )
            )
        for target, updates, validated_evidence in validated_updates:
            for key, value in updates.items():
                setattr(target, key, value)
            target.field_evidence_json = validated_evidence
        try:
            session.flush()
        except (IntegrityError, StatementError) as exc:
            raise ReviewDecisionError("invalid_correction_value") from exc
        validation = ValidationService(self.settings).evaluate_document(session, document, records)
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
                "field_evidence": [
                    FieldEvidenceService.summary(record.field_evidence_json) for record in records
                ],
            }
            document.metadata_json = metadata
            document.corrected_fields_json = {
                **document.corrected_fields_json,
                "records": corrections["records"],
            }
        for record in records:
            if isinstance(record, RegulatoryCase):
                record.evidence_quality = quality
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
        self._release_reservation(
            session,
            item.record_type,
            item.record_id,
            "review_decided",
        )
        if authenticity_decision:
            self._verify_authenticity(
                session,
                document,
                decision,
                reviewer,
                authenticity_decision,
            )
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
        self._release_reservation(
            session,
            item.record_type,
            item.record_id,
            "review_decided",
        )
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

    def _verify_review_package(self, batch: ReviewBatch) -> None:
        path = Path(batch.export_path)
        if (
            not path.is_file()
            or not batch.export_sha256
            or hashlib.sha256(path.read_bytes()).hexdigest() != batch.export_sha256
        ):
            raise ReviewDecisionError("review_package_tampered")
        if batch.bundle_path:
            self._verify_bundle(batch, path.read_bytes())

    def _verify_authenticity(
        self,
        session: Session,
        document: SourceDocument,
        decision: ReviewDecision,
        reviewer: str,
        authenticity_decision: AuthenticityDecisionInput,
    ) -> None:
        occurrence = session.get(DocumentOccurrence, authenticity_decision.verified_occurrence_id)
        if not occurrence or occurrence.document_id != document.id:
            raise ReviewDecisionError(
                "verified occurrence does not belong to the reviewed document"
            )
        if occurrence.source_id is None:
            raise ReviewDecisionError("verified occurrence has no registered source")
        source = session.get(DataSource, occurrence.source_id)
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
        RawArtifactIntegrityService(self.settings).verify(document)
        ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        allowed_domains = source.crawl_policy.get("allowed_domains", [])
        if not isinstance(allowed_domains, list) or any(
            not isinstance(value, str) for value in allowed_domains
        ):
            raise ReviewDecisionError("Registered source allowed_domains is invalid")
        allowed_hosts = SafeUrlPolicy.allowed_hosts(source.base_url, allowed_domains)
        urls = [occurrence.source_url, occurrence.final_url]
        if any(
            not url
            or urlparse(url).scheme not in {"http", "https"}
            or not SafeUrlPolicy.host_allowed_for_hosts(url, allowed_hosts)
            for url in urls
        ):
            raise ReviewDecisionError(
                "Occurrence URL does not match the registered source allowlist"
            )
        records = StateMachineService.structured_records(session, document)
        if not records or any(
            not getattr(record, "source_quote", None)
            or record.source_quote not in (document.raw_text or "")
            for record in records
        ):
            raise ReviewDecisionError("source_quote cannot be located in immutable raw text")
        for record in records:
            try:
                FieldEvidenceService(self.settings).validate(
                    session,
                    document,
                    {
                        field_name: getattr(record, field_name)
                        for field_name in EVIDENCE_FIELDS[document.data_type]
                    },
                    record.field_evidence_json,
                )
            except FieldEvidenceError as exc:
                raise ReviewDecisionError(str(exc)) from exc
        previous = document.authenticity_type
        document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
        session.add(
            AuthenticityDecisionLog(
                document_id=document.id,
                previous_authenticity_type=previous,
                new_authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
                reviewer=reviewer,
                review_decision_id=decision.id,
                source_id=source.id,
                verified_occurrence_id=occurrence.id,
                reason=authenticity_decision.reason,
            )
        )

    @staticmethod
    def _pending_records(session: Session, data_type: str) -> list[Any]:
        reviewable_statuses = (
            ReviewStatus.PENDING_REVIEW.value,
            ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
        )
        if data_type == DataType.EVALUATION_SAMPLE.value:
            return list(
                session.scalars(
                    select(EvaluationSample).where(
                        EvaluationSample.final_review_status.in_(reviewable_statuses)
                    )
                )
            )
        return list(
            session.scalars(
                select(SourceDocument)
                .where(
                    SourceDocument.final_review_status.in_(reviewable_statuses),
                    SourceDocument.data_type == data_type,
                )
                .order_by(SourceDocument.id)
            )
        )

    def _document_review_row(
        self,
        session: Session,
        document: SourceDocument,
        batch_id: int,
        batch_item_id: int,
    ) -> dict[str, Any]:
        records = StateMachineService.structured_records(session, document)
        pilot_ids = list(
            session.scalars(
                select(PilotCollectionItem.pilot_id)
                .where(PilotCollectionItem.document_id == document.id)
                .order_by(PilotCollectionItem.id)
            )
        )
        index_rejection_reasons = KnowledgeIndexService(self.settings).rejection_reasons(
            session, document
        )
        raw_provenance = document.metadata_json.get("structured_draft_provenance", [])
        provenance_by_record_id = {
            item["structured_record_id"]: {
                key: value
                for key, value in item.items()
                if key
                in {
                    "pilot_id",
                    "draft_generation_method",
                    "draft_generation_version",
                }
            }
            for item in raw_provenance
            if isinstance(item, dict) and isinstance(item.get("structured_record_id"), int)
        }
        parsed_records = []
        for record in records:
            parsed_record = {
                (
                    "structured_record_id"
                    if key == "id"
                    else "field_evidence"
                    if key == "field_evidence_json"
                    else key
                ): value
                for key, value in vars(record).items()
                if not key.startswith("_") and key != "document_id"
            }
            parsed_record["draft_provenance"] = provenance_by_record_id.get(record.id)
            parsed_records.append(parsed_record)
        return {
            "batch_id": batch_id,
            "batch_item_id": batch_item_id,
            "pilot_id": pilot_ids[0] if len(pilot_ids) == 1 else None,
            "pilot_ids": pilot_ids,
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
            "parsed_artifact_path": document.parsed_artifact_path,
            "parsed_artifact_sha256": document.parsed_artifact_sha256,
            "parsed_text_sha256": document.parsed_text_sha256,
            "parsed_from_raw_sha256": document.parsed_from_raw_sha256,
            "parser_name": document.parser_name,
            "parser_version": document.parser_version,
            "parsed_fields": {"records": parsed_records},
            "source_quotes": [
                getattr(record, "source_quote", None)
                for record in records
                if getattr(record, "source_quote", None)
            ],
            "source_occurrences": [
                {
                    "occurrence_id": occurrence.id,
                    "source_id": occurrence.source_id,
                    "source_url": occurrence.source_url,
                    "final_url": occurrence.final_url,
                    "publisher": occurrence.publisher,
                    "collected_at": occurrence.collected_at,
                    "response_metadata": occurrence.response_metadata,
                }
                for occurrence in sorted(document.occurrences, key=lambda value: value.id)
            ],
            "automatic_validation": document.metadata_json.get("automatic_validation", {}),
            "parsing_warnings": document.metadata_json.get("parsing", {}).get("warnings", []),
            "authenticity_type": document.authenticity_type,
            "current_status": document.final_review_status,
            "knowledge_index_status": document.knowledge_index_status,
            "can_index": not index_rejection_reasons,
            "index_rejection_reasons": index_rejection_reasons,
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
            "source_occurrences": [],
            "automatic_validation": {"valid": True, "issues": []},
            "parsing_warnings": [],
            "authenticity_type": sample.authenticity_type,
            "current_status": sample.final_review_status,
        }

    @staticmethod
    def _result_template_rows(
        batch: ReviewBatch, items: list[ReviewBatchItem]
    ) -> list[dict[str, Any]]:
        return [
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

    @classmethod
    def _portable_occurrences(cls, occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **occurrence,
                "source_url": cls._portable_url(occurrence.get("source_url")),
                "final_url": cls._portable_url(occurrence.get("final_url")),
                "response_metadata": {
                    key: value
                    for key, value in (occurrence.get("response_metadata") or {}).items()
                    if "path" not in key.lower()
                },
            }
            for occurrence in occurrences
        ]

    @staticmethod
    def _portable_url(value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith("file://"):
            return value
        return f"local-unattributed://{Path(urlparse(value).path).name}"

    @staticmethod
    def _bundle_payload_hash(
        review_bytes: bytes,
        template_bytes: bytes,
        artifact_members: dict[str, bytes],
    ) -> str:
        digest = hashlib.sha256()
        for name, content in [
            ("review.jsonl", review_bytes),
            ("review-results-template.jsonl", template_bytes),
            *sorted(artifact_members.items()),
        ]:
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()

    def _verify_bundle(self, batch: ReviewBatch, expected_review: bytes) -> None:
        try:
            bundle_path = Path(batch.bundle_path or "")
            if (
                not bundle_path.is_file()
                or not batch.bundle_sha256
                or hashlib.sha256(bundle_path.read_bytes()).hexdigest() != batch.bundle_sha256
            ):
                raise ValueError
            with zipfile.ZipFile(bundle_path) as bundle:
                names = set(bundle.namelist())
                if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                    raise ValueError
                manifest_bytes = bundle.read("manifest.json")
                review_bytes = bundle.read("review.jsonl")
                template_bytes = bundle.read("review-results-template.jsonl")
                manifest = json.loads(manifest_bytes)
                if (
                    not batch.bundle_manifest_sha256
                    or hashlib.sha256(manifest_bytes).hexdigest() != batch.bundle_manifest_sha256
                    or review_bytes != expected_review
                    or manifest["batch_id"] != batch.id
                    or manifest["schema_version"] != batch.schema_version
                    or hashlib.sha256(review_bytes).hexdigest() != manifest["review_jsonl_sha256"]
                    or hashlib.sha256(template_bytes).hexdigest()
                    != manifest["result_template_sha256"]
                ):
                    raise ValueError
                rows = {
                    int(row["batch_item_id"]): row
                    for row in (
                        json.loads(line)
                        for line in review_bytes.decode().splitlines()
                        if line.strip()
                    )
                }
                artifact_members: dict[str, bytes] = {}
                for item in manifest["items"]:
                    row = rows[int(item["batch_item_id"])]
                    if (
                        payload_hash(row) != item["payload_hash"]
                        or row["raw_file_path"] != item["raw_bundle_relative_path"]
                        or row["parsed_artifact_path"] != item["parsed_bundle_relative_path"]
                    ):
                        raise ValueError
                    raw_member = item["raw_bundle_relative_path"]
                    raw_content = bundle.read(raw_member)
                    if hashlib.sha256(raw_content).hexdigest() != item["raw_sha256"]:
                        raise ValueError
                    parsed_member = item["parsed_bundle_relative_path"]
                    parsed_content = bundle.read(parsed_member)
                    if hashlib.sha256(parsed_content).hexdigest() != item["parsed_artifact_sha256"]:
                        raise ValueError
                    artifact_members[raw_member] = raw_content
                    artifact_members[parsed_member] = parsed_content
                if (
                    self._bundle_payload_hash(review_bytes, template_bytes, artifact_members)
                    != manifest["package_sha256"]
                ):
                    raise ValueError
        except (
            KeyError,
            TypeError,
            ValueError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ) as exc:
            raise ReviewDecisionError("review_package_tampered") from exc

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
    def _release_reservation(
        session: Session,
        record_type: str,
        record_id: int,
        reason: str,
    ) -> None:
        reservation = session.scalar(
            select(ReviewReservation).where(
                ReviewReservation.record_type == record_type,
                ReviewReservation.record_id == record_id,
                ReviewReservation.status == "active",
            )
        )
        if reservation:
            reservation.status = "released"
            reservation.released_at = datetime.now(UTC)
            reservation.release_reason = reason

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
