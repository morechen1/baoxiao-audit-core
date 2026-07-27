from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import FieldEvidenceError
from app.models import SourceDocument
from app.models.enums import DataType
from app.schemas.structured import FieldEvidenceItem
from app.services.parsed_artifacts import ParsedArtifactIntegrityService

EVIDENCE_FIELDS: dict[str, frozenset[str]] = {
    DataType.REGULATION.value: frozenset(
        {
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
            "authority",
            "document_number",
            "decision_date",
            "illegal_facts",
            "legal_basis",
            "penalty_result",
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
}


class FieldEvidenceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def validate(
        self,
        session: Session,
        document: SourceDocument,
        fields: dict[str, Any],
        evidence_payload: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        artifact = ParsedArtifactIntegrityService(self.settings).verify(document, session=session)
        allowed = EVIDENCE_FIELDS.get(document.data_type)
        if allowed is None:
            raise FieldEvidenceError("unsupported_evidence_record_type")
        required = {
            name
            for name in allowed
            if name in fields and fields[name] is not None and str(fields[name]).strip()
        }
        if not required.issubset(evidence_payload):
            raise FieldEvidenceError("missing_field_evidence")
        unknown = set(evidence_payload) - allowed
        if unknown:
            raise FieldEvidenceError("field_not_supported_by_evidence")
        validated: dict[str, list[dict[str, Any]]] = {}
        for field_name, raw_items in evidence_payload.items():
            if not isinstance(raw_items, list) or not raw_items:
                raise FieldEvidenceError("missing_field_evidence")
            try:
                items = [FieldEvidenceItem.model_validate(item) for item in raw_items]
            except PydanticValidationError as exc:
                raise FieldEvidenceError("field_not_supported_by_evidence") from exc
            self._validate_ranges(items)
            for item in items:
                self._validate_item(
                    session,
                    document,
                    artifact,
                    field_name,
                    fields.get(field_name),
                    item,
                )
            validated[field_name] = [
                item.model_dump(mode="json", exclude_none=True) for item in items
            ]
        return validated

    @staticmethod
    def _validate_ranges(items: list[FieldEvidenceItem]) -> None:
        ordered = sorted(items, key=lambda item: (item.start_offset, item.end_offset))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_offset < previous.end_offset:
                raise FieldEvidenceError("evidence_range_overlap")

    def _validate_item(
        self,
        session: Session,
        document: SourceDocument,
        artifact: dict[str, Any],
        field_name: str,
        field_value: Any,
        item: FieldEvidenceItem,
    ) -> None:
        if item.document_id is not None and item.document_id != document.id:
            raise FieldEvidenceError("evidence_document_mismatch")
        raw_text = document.raw_text or ""
        if raw_text[item.start_offset : item.end_offset] != item.quote:
            raise FieldEvidenceError("evidence_offset_mismatch")
        page = next(
            (
                value
                for value in artifact.get("pages", [])
                if value.get("page_number") == item.page_number
            ),
            None,
        )
        if (
            not page
            or item.start_offset < int(page["start_offset"])
            or item.end_offset > int(page["end_offset"])
            or page["text"][
                item.start_offset - int(page["start_offset"]) : item.end_offset
                - int(page["start_offset"])
            ]
            != item.quote
        ):
            raise FieldEvidenceError("evidence_page_mismatch")
        if item.chunk_id is not None:
            chunk = next((value for value in document.chunks if value.id == item.chunk_id), None)
            if (
                not chunk
                or chunk.document_id != document.id
                or chunk.start_offset > item.start_offset
                or chunk.end_offset < item.end_offset
            ):
                raise FieldEvidenceError("evidence_chunk_mismatch")
        if item.mode == "summary":
            raise FieldEvidenceError("summary_evidence_requires_expert_review")
        if item.mode == "document_metadata":
            self._validate_metadata(document, field_name, field_value, item)
            return
        value = self._string_value(field_value)
        if item.mode == "verbatim":
            normalized_value = self._basic_normalize(value)
            normalized_quote = self._basic_normalize(item.quote)
            if normalized_value != normalized_quote and normalized_value not in normalized_quote:
                raise FieldEvidenceError("field_not_supported_by_evidence")
            return
        transformed = self._apply_declared_transform(item.quote, item.transformation_note or "")
        if self._basic_normalize(value) != self._basic_normalize(transformed):
            raise FieldEvidenceError("field_not_supported_by_evidence")

    @staticmethod
    def _validate_metadata(
        document: SourceDocument,
        field_name: str,
        field_value: Any,
        item: FieldEvidenceItem,
    ) -> None:
        allowed = {
            "title": {"source_title", "filename"},
            "issuing_authority": {"publisher"},
            "authority": {"publisher"},
            "effective_date": {"published_at"},
            "decision_date": {"published_at"},
        }
        if item.metadata_field not in allowed.get(field_name, set()):
            raise FieldEvidenceError("field_not_supported_by_evidence")
        metadata_value: Any
        if item.metadata_field == "filename":
            metadata_value = document.raw_file_path.rsplit("/", 1)[-1]
        else:
            metadata_value = getattr(document, item.metadata_field or "", None)
        if FieldEvidenceService._basic_normalize(
            FieldEvidenceService._string_value(field_value)
        ) != FieldEvidenceService._basic_normalize(
            FieldEvidenceService._string_value(metadata_value)
        ):
            raise FieldEvidenceError("field_not_supported_by_evidence")

    @staticmethod
    def _string_value(value: Any) -> str:
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _basic_normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value)
        punctuation: dict[str, str | int | None] = {
            "，": ",",
            "。": ".",
            "：": ":",
            "；": ";",
        }
        value = value.translate(str.maketrans(punctuation))
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _apply_declared_transform(cls, quote: str, note: str) -> str:
        normalized_note = note.strip()
        value = quote
        label_match = re.search(r"[“\"](.+?)[”\"]", normalized_note)
        if normalized_note.startswith("移除字段标签") and label_match:
            label = label_match.group(1)
            if not value.startswith(label):
                raise FieldEvidenceError("field_not_supported_by_evidence")
            value = value[len(label) :]
        elif normalized_note in {"去除多余空白", "whitespace_normalized"}:
            value = re.sub(r"\s+", " ", value).strip()
        elif normalized_note in {"全半角统一", "full_width_normalized"}:
            value = unicodedata.normalize("NFKC", value)
        elif normalized_note in {"标点标准化", "punctuation_normalized"}:
            punctuation: dict[str, str | int | None] = {
                "，": ",",
                "。": ".",
                "：": ":",
                "；": ";",
            }
            value = value.translate(str.maketrans(punctuation))
        elif normalized_note in {"日期标准化", "date_normalized"}:
            match = re.search(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?", value)
            if not match:
                raise FieldEvidenceError("field_not_supported_by_evidence")
            value = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        elif normalized_note in {"列表拆分与合并", "list_normalized"}:
            value = ",".join(
                part.strip() for part in re.split(r"[,，;；、]", value) if part.strip()
            )
        else:
            raise FieldEvidenceError("field_not_supported_by_evidence")
        return value.strip()

    @staticmethod
    def summary(evidence: dict[str, Any]) -> dict[str, Any]:
        return {
            field: {
                "count": len(items) if isinstance(items, list) else 0,
                "modes": sorted(
                    {
                        str(item.get("mode"))
                        for item in items
                        if isinstance(item, dict) and item.get("mode")
                    }
                )
                if isinstance(items, list)
                else [],
            }
            for field, items in evidence.items()
        }
