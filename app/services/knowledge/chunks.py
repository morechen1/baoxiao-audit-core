from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.models import Penalty, ProductDocument, Regulation, SourceDocument
from app.services.knowledge.normalization import (
    NORMALIZATION_VERSION,
    TOKENIZER_VERSION,
    lexical_tokens_text,
    trusted_lexical_normalize,
)
from app.services.review_payload import canonical_json_bytes, portable_record_key

CHUNKER_VERSION = "trusted_structured_chunker_v1"
RANKING_VERSION = "trusted_lexical_rank_v1"


@dataclass(frozen=True)
class ChunkCandidate:
    record_type: str
    structured_record_id: int
    pilot_id: str | None
    portable_record_key: str
    chunk_kind: str
    chunk_ordinal: int
    title: str
    text: str
    normalized_text: str
    lexical_tokens: str
    authority: str | None
    relevant_date: date | None
    source_url: str
    source_locator: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    evidence_quality: str | None
    source_payload_hash: str
    chunk_content_sha256: str
    chunk_identity_sha256: str


def build_record_chunks(
    document: SourceDocument,
    record: Regulation | ProductDocument | Penalty,
    *,
    pilot_id: str | None,
    provenance: dict[str, Any] | None,
    verified_source: dict[str, Any],
    evidence_quality: str | None,
) -> list[ChunkCandidate]:
    record_type = document.data_type
    record_payload = _record_payload(record, provenance)
    record_key = portable_record_key(document.sha256, record_type, record_payload)
    source_locator = {
        "source_url": verified_source["source_url"],
        "final_url": verified_source["final_url"],
        "raw_artifact_sha256": document.sha256,
        "parsed_artifact_sha256": document.parsed_artifact_sha256,
        "source_entry_index": getattr(record, "source_entry_index", None),
        "source_entry_fingerprint": getattr(record, "source_entry_fingerprint", None),
        "source_entry_locator": (provenance or {}).get("source_entry_locator"),
        "punished_entity": getattr(record, "punished_entity", None),
        "document_number": getattr(record, "document_number", None),
    }
    specs = _chunk_specs(record)
    candidates: list[ChunkCandidate] = []
    for ordinal, (kind, title, text, fields, authority, relevant_date) in enumerate(specs):
        normalized = trusted_lexical_normalize(text)
        if not normalized:
            continue
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        identity = {
            "raw_artifact_sha256": document.sha256,
            "record_type": record_type,
            "record_identity": record_key,
            "chunk_kind": kind,
            "chunk_ordinal": ordinal,
            "chunk_content_sha256": content_hash,
            "normalization_version": NORMALIZATION_VERSION,
            "tokenizer_version": TOKENIZER_VERSION,
            "chunker_version": CHUNKER_VERSION,
        }
        candidates.append(
            ChunkCandidate(
                record_type=record_type,
                structured_record_id=record.id,
                pilot_id=pilot_id,
                portable_record_key=record_key,
                chunk_kind=kind,
                chunk_ordinal=ordinal,
                title=title,
                text=text,
                normalized_text=normalized,
                lexical_tokens=lexical_tokens_text(text),
                authority=authority,
                relevant_date=relevant_date,
                source_url=str(verified_source["source_url"]),
                source_locator={
                    key: value for key, value in source_locator.items() if value is not None
                },
                evidence_references=_evidence_references(record.field_evidence_json, fields),
                evidence_quality=evidence_quality,
                source_payload_hash=record_key,
                chunk_content_sha256=content_hash,
                chunk_identity_sha256=hashlib.sha256(canonical_json_bytes(identity)).hexdigest(),
            )
        )
    return candidates


def recompute_chunk_identity(
    *,
    raw_artifact_sha256: str,
    record_type: str,
    portable_record_key_value: str,
    chunk_kind: str,
    chunk_ordinal: int,
    chunk_content_sha256: str,
    chunker_version: str,
    tokenizer_version: str,
) -> str:
    identity = {
        "raw_artifact_sha256": raw_artifact_sha256,
        "record_type": record_type,
        "record_identity": portable_record_key_value,
        "chunk_kind": chunk_kind,
        "chunk_ordinal": chunk_ordinal,
        "chunk_content_sha256": chunk_content_sha256,
        "normalization_version": NORMALIZATION_VERSION,
        "tokenizer_version": tokenizer_version,
        "chunker_version": chunker_version,
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _record_payload(
    record: Regulation | ProductDocument | Penalty,
    provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(record, Regulation):
        fields: dict[str, Any] = {
            "title": record.title,
            "document_number": record.document_number,
            "issuing_authority": record.issuing_authority,
            "effective_date": record.effective_date,
            "expiry_date": record.expiry_date,
            "validity_status": record.validity_status,
            "article_number": record.article_number,
            "article_text": record.article_text,
            "source_quote": record.source_quote,
        }
    elif isinstance(record, ProductDocument):
        fields = {
            "company_name": record.company_name,
            "product_name": record.product_name,
            "product_type": record.product_type,
            "waiting_period": record.waiting_period,
            "cooling_off_period": record.cooling_off_period,
            "insurance_responsibility": record.insurance_responsibility,
            "exclusions": record.exclusions,
            "cash_value_description": record.cash_value_description,
            "guaranteed_benefit": record.guaranteed_benefit,
            "non_guaranteed_benefit": record.non_guaranteed_benefit,
            "surrender_risk": record.surrender_risk,
            "source_quote": record.source_quote,
        }
    else:
        fields = {
            "source_entry_index": record.source_entry_index,
            "source_entry_fingerprint": record.source_entry_fingerprint,
            "punished_entity": record.punished_entity,
            "authority": record.authority,
            "document_number": record.document_number,
            "decision_date": record.decision_date,
            "illegal_facts": record.illegal_facts,
            "legal_basis": record.legal_basis,
            "penalty_result": record.penalty_result,
            "original_sales_wording_disclosed": record.original_sales_wording_disclosed,
            "original_sales_wording": record.original_sales_wording,
            "source_quote": record.source_quote,
        }
    return {
        **fields,
        "field_evidence": record.field_evidence_json,
        "draft_provenance": provenance,
    }


def _chunk_specs(
    record: Regulation | ProductDocument | Penalty,
) -> list[tuple[str, str, str, set[str], str | None, date | None]]:
    if isinstance(record, Regulation):
        title = record.title
        result = [
            (
                "basic_information",
                title,
                _lines(
                    (
                        ("标题", record.title),
                        ("文号", record.document_number),
                        ("发布机关", record.issuing_authority),
                        ("生效日期", record.effective_date),
                        ("失效日期", record.expiry_date),
                    )
                ),
                {"title", "document_number", "issuing_authority", "effective_date", "expiry_date"},
                record.issuing_authority,
                record.effective_date,
            )
        ]
        for part in _paragraph_chunks(record.article_text):
            result.append(
                (
                    "article_text",
                    title,
                    _lines((("条款", record.article_number), ("正文", part))),
                    {"article_number", "article_text"},
                    record.issuing_authority,
                    record.effective_date,
                )
            )
        structure = _lines(
            (
                ("条款编号", record.article_number),
                ("效力状态", record.validity_status),
                ("发布机关", record.issuing_authority),
            )
        )
        if structure:
            result.append(
                (
                    "scope_and_status",
                    title,
                    structure,
                    {"article_number", "issuing_authority"},
                    record.issuing_authority,
                    record.effective_date,
                )
            )
        return result
    if isinstance(record, ProductDocument):
        title = record.product_name
        specs = [
            (
                "product_identity",
                title,
                _lines(
                    (
                        ("产品名称", record.product_name),
                        ("保险公司", record.company_name),
                        ("产品类型", record.product_type),
                    )
                ),
                {"product_name", "company_name", "product_type"},
                record.company_name,
                None,
            ),
            (
                "coverage",
                title,
                _lines(
                    (
                        ("保险责任", record.insurance_responsibility),
                        ("保证利益", record.guaranteed_benefit),
                        ("非保证利益", record.non_guaranteed_benefit),
                    )
                ),
                {"insurance_responsibility", "guaranteed_benefit", "non_guaranteed_benefit"},
                record.company_name,
                None,
            ),
            (
                "terms_and_risks",
                title,
                _lines(
                    (
                        ("等待期", record.waiting_period),
                        ("犹豫期", record.cooling_off_period),
                        ("责任免除", record.exclusions),
                        ("现金价值", record.cash_value_description),
                        ("退保风险", record.surrender_risk),
                    )
                ),
                {
                    "waiting_period",
                    "cooling_off_period",
                    "exclusions",
                    "cash_value_description",
                    "surrender_risk",
                },
                record.company_name,
                None,
            ),
        ]
        return [spec for spec in specs if spec[2]]
    title = record.punished_entity or "行政处罚"
    return [
        (
            "penalty_entry",
            title,
            _lines(
                (
                    ("被处罚对象", record.punished_entity),
                    ("处罚机关", record.authority),
                    ("处罚文号", record.document_number),
                    ("决定日期", record.decision_date),
                    ("违法事实", record.illegal_facts),
                    ("法律依据", record.legal_basis),
                    ("处罚结果", record.penalty_result),
                    ("原销售话术", record.original_sales_wording),
                )
            ),
            {
                "punished_entity",
                "authority",
                "document_number",
                "decision_date",
                "illegal_facts",
                "legal_basis",
                "penalty_result",
                "original_sales_wording",
            },
            record.authority,
            record.decision_date,
        )
    ]


def _lines(values: tuple[tuple[str, object | None], ...]) -> str:
    return "\n".join(
        f"{label}：{value.isoformat() if isinstance(value, date) else value}"
        for label, value in values
        if value is not None and str(value).strip()
    )


def _paragraph_chunks(value: str, limit: int = 1200) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", value) if part.strip()]
    result: list[str] = []
    for paragraph in paragraphs or [value.strip()]:
        sentences = [part for part in re.split(r"(?<=[。！？；])", paragraph) if part]
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > limit:
                result.append(current)
                current = sentence
            else:
                current += sentence
        if current:
            result.append(current)
    return result


def _evidence_references(
    evidence: dict[str, list[dict[str, Any]]], fields: set[str]
) -> list[dict[str, Any]]:
    result = []
    for field_name in sorted(fields):
        for item in evidence.get(field_name, []):
            result.append(
                {
                    "field_name": field_name,
                    **{
                        key: item[key]
                        for key in (
                            "quote",
                            "page_number",
                            "start_offset",
                            "end_offset",
                            "mode",
                            "transformation_note",
                            "metadata_field",
                        )
                        if key in item and item[key] is not None
                    },
                }
            )
    return result
