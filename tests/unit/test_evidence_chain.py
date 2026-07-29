import hashlib
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import get_db
from app.core.config import Settings
from app.core.exceptions import (
    FieldEvidenceError,
    ParsedArtifactIntegrityError,
    ReviewDecisionError,
    StructuredRecordError,
)
from app.main import app
from app.models import (
    DocumentChunk,
    ParsedArtifactVersion,
    Penalty,
    ProductDocument,
    Regulation,
    ReviewBatch,
    ReviewReservation,
    SourceDocument,
    StructuredDraftRevision,
)
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.schemas.structured import (
    RegulationDraft,
    StructuredDraftEnvelope,
    StructuredDraftRevisionEnvelope,
)
from app.services.field_evidence import (
    HAN_ORGANIZATION_NAME_TRANSFORMATION,
    FieldEvidenceService,
)
from app.services.knowledge import KnowledgeIndexService
from app.services.parsed_artifacts import (
    ParsedArtifactIntegrityService,
    ParsedArtifactService,
)
from app.services.parsing import ParsingService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.penalty_entries import (
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
)
from app.services.review import ReviewService
from app.services.review.service import payload_hash
from app.services.structured_records import StructuredRecordService
from app.services.structured_revisions import StructuredDraftRevisionService
from app.services.validation import ValidationService


def settings_for(session) -> Settings:
    return Settings(database_url="sqlite://", data_dir=session.info["data_dir"])


def make_document(
    session,
    data_type: str,
    text: str,
    *,
    status: str = ReviewStatus.PARSED.value,
    authenticity: str = AuthenticityType.PENDING_VERIFICATION.value,
) -> SourceDocument:
    serial = session.query(SourceDocument).count() + 1
    content = f"immutable-source-{serial}\n{text}".encode()
    digest = hashlib.sha256(content).hexdigest()
    raw_dir = session.info["data_dir"] / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{digest}.txt"
    raw_path.write_bytes(content)
    document = SourceDocument(
        data_type=data_type,
        source_url=f"https://official.example/{data_type}/{serial}",
        final_url=f"https://official.example/{data_type}/{serial}",
        raw_file_path=str(raw_path),
        raw_text=text,
        sha256=digest,
        authenticity_type=authenticity,
        parse_status="parsed",
        final_review_status=status,
        metadata_json={},
    )
    session.add(document)
    session.flush()
    document.chunks.append(
        DocumentChunk(
            page_number=1,
            chunk_index=0,
            text=text,
            start_offset=0,
            end_offset=len(text),
        )
    )
    ParsedArtifactService(settings_for(session)).persist(
        session,
        document,
        ParsedDocument(
            title="证据链测试",
            plain_text=text,
            pages=[ParsedPage(page_number=1, text=text)],
        ),
        parser_name="TestParser",
    )
    session.commit()
    return document


def evidence(
    document: SourceDocument,
    quote: str,
    *,
    field_document_id: int | None = None,
    mode: str = "verbatim",
    note: str | None = None,
    start_delta: int = 0,
) -> list[dict[str, object]]:
    start = (document.raw_text or "").index(quote) + start_delta
    item: dict[str, object] = {
        "quote": quote,
        "page_number": 1,
        "start_offset": start,
        "end_offset": start + len(quote),
        "mode": mode,
    }
    if field_document_id is not None:
        item["document_id"] = field_document_id
    if note is not None:
        item["transformation_note"] = note
    return [item]


def record_fields(data_type: str, value: str) -> dict[str, object]:
    if data_type == DataType.REGULATION.value:
        return {
            "title": "正式规则",
            "article_text": value,
            "source_quote": "真实原文",
        }
    if data_type == DataType.PENALTY.value:
        return {
            "punished_entity": "真实原文",
            "illegal_facts": value,
            "original_sales_wording_disclosed": False,
            "original_sales_wording": None,
            "source_quote": "真实原文",
        }
    return {"product_name": value, "source_quote": "真实原文"}


def envelope_for(
    document: SourceDocument,
    value: str,
    field_name: str,
    field_evidence: dict[str, list[dict[str, object]]] | None,
) -> StructuredDraftEnvelope:
    fields = record_fields(document.data_type, value)
    payload: dict[str, object] = {
        "document_id": document.id,
        "record_type": document.data_type,
        "fields": fields,
        "field_evidence": field_evidence,
    }
    if document.data_type == DataType.PENALTY.value:
        locator = {"table_index": 1, "logical_row": 1}
        identity_evidence = dict(field_evidence or {})
        identity_evidence.setdefault("punished_entity", evidence(document, "真实原文"))
        identity_evidence.setdefault("illegal_facts", evidence(document, "真实原文"))
        if field_evidence is not None:
            field_evidence.setdefault("punished_entity", evidence(document, "真实原文"))
        try:
            fragments = build_penalty_source_entry_fragments(fields, identity_evidence)
            content_sha256 = source_entry_content_sha256(
                build_penalty_identity_material(fields, identity_evidence)
            )
        except ValueError:
            # This helper intentionally constructs invalid field-evidence envelopes so
            # the import boundary, rather than the identity builder, is under test.
            fragments = [
                {
                    "quote": "真实原文",
                    "start_offset": 0,
                    "end_offset": len("真实原文"),
                }
            ]
            content_sha256 = "a" * 64
        fields.update(
            {
                "source_entry_index": 1,
                "source_entry_fingerprint": penalty_source_entry_fingerprint(
                    raw_artifact_sha256=document.sha256,
                    source_entry_content_sha256=content_sha256,
                ),
            }
        )
        payload.update(
            {
                "source_entry_locator": locator,
                "source_entry_fragments": fragments,
                "source_entry_content_sha256": content_sha256,
            }
        )
    return StructuredDraftEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("data_type", "field_name"),
    [
        (DataType.REGULATION.value, "article_text"),
        (DataType.PENALTY.value, "illegal_facts"),
        (DataType.PRODUCT_DOCUMENT.value, "product_name"),
    ],
)
def test_true_source_quote_cannot_support_fabricated_field(
    session,
    data_type: str,
    field_name: str,
) -> None:
    raw_text = "正式规则 真实原文" if data_type == DataType.REGULATION.value else "真实原文"
    document = make_document(session, data_type, raw_text)
    field_evidence = {field_name: evidence(document, "真实原文")}
    if data_type == DataType.REGULATION.value:
        field_evidence["title"] = evidence(document, "正式规则")
    draft = envelope_for(
        document,
        "虚构内容",
        field_name,
        field_evidence,
    )

    with pytest.raises(StructuredRecordError, match="field_not_supported_by_evidence"):
        StructuredRecordService(settings_for(session)).import_draft(session, draft)


def test_new_public_draft_requires_field_evidence(session) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "真实原文")
    draft = envelope_for(document, "真实原文", "product_name", None)

    with pytest.raises(StructuredRecordError, match="missing_field_evidence"):
        StructuredRecordService(settings_for(session)).import_draft(session, draft)


@pytest.mark.parametrize("start_delta", [1, -1])
def test_evidence_offset_mismatch_is_rejected(session, start_delta: int) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "甲真实原文乙")
    payload = {"product_name": evidence(document, "真实原文", start_delta=start_delta)}

    with pytest.raises(FieldEvidenceError, match="evidence_offset_mismatch"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"product_name": "真实原文"},
            payload,
        )


def test_evidence_cannot_point_to_another_document(session) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "真实原文")
    other = make_document(session, DataType.PRODUCT_DOCUMENT.value, "另一原文")

    with pytest.raises(FieldEvidenceError, match="evidence_document_mismatch"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"product_name": "真实原文"},
            {
                "product_name": evidence(
                    document,
                    "真实原文",
                    field_document_id=other.id,
                )
            },
        )


def test_verbatim_value_must_match_quote(session) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "真实原文")

    with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"product_name": "不受支持"},
            {"product_name": evidence(document, "真实原文")},
        )


def test_normalized_value_must_follow_declared_transform(session) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "产品名称：安心保险")

    with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"product_name": "另一产品"},
            {
                "product_name": evidence(
                    document,
                    "产品名称：安心保险",
                    mode="normalized",
                    note="移除字段标签“产品名称：”",
                )
            },
        )


def test_split_chinese_date_whitespace_is_deterministically_normalized(session) -> None:
    text = "本办法自\n202\n4\n年\n3\n月\n1\n日起施行。"
    document = make_document(session, DataType.REGULATION.value, text)

    validated = FieldEvidenceService(settings_for(session)).validate(
        session,
        document,
        {"effective_date": date(2024, 3, 1)},
        {
            "effective_date": evidence(
                document,
                text,
                mode="normalized",
                note="collapse_unicode_whitespace_for_chinese_date_v1",
            )
        },
    )

    assert validated["effective_date"][0]["quote"] == text
    assert validated["effective_date"][0]["transformation_note"] == (
        "collapse_unicode_whitespace_for_chinese_date_v1"
    )


def test_split_chinese_date_requires_exact_quote_and_single_sentence(session) -> None:
    text = "本办法自\n202\n4\n年\n3\n月\n1\n日起施行。"
    document = make_document(session, DataType.REGULATION.value, text)
    fabricated_quote = "本办法自2024年3月1日起施行。"
    fabricated = {
        "effective_date": [
            {
                "quote": fabricated_quote,
                "page_number": 1,
                "start_offset": 0,
                "end_offset": len(fabricated_quote),
                "mode": "normalized",
                "transformation_note": "collapse_unicode_whitespace_for_chinese_date_v1",
            }
        ]
    }

    with pytest.raises(FieldEvidenceError, match="evidence_offset_mismatch"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"effective_date": date(2024, 3, 1)},
            fabricated,
        )

    cross_sentence = "本办法自202\n4年。另文3月1日起施行"
    other = make_document(session, DataType.REGULATION.value, cross_sentence)
    with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            other,
            {"effective_date": date(2024, 3, 1)},
            {
                "effective_date": evidence(
                    other,
                    cross_sentence,
                    mode="normalized",
                    note="collapse_unicode_whitespace_for_chinese_date_v1",
                )
            },
        )


def test_han_organization_name_normalizes_exact_multiline_quote(session) -> None:
    quote = "中\n国银\n保监\n会消费者权益保护局"
    target = "中国银保监会消费者权益保护局"
    document = make_document(session, DataType.REGULATORY_CASE.value, quote)

    validated = FieldEvidenceService(settings_for(session)).validate(
        session,
        document,
        {"publisher": target},
        {
            "publisher": evidence(
                document,
                quote,
                mode="normalized",
                note=HAN_ORGANIZATION_NAME_TRANSFORMATION,
            )
        },
    )

    assert validated["publisher"][0]["quote"] == quote
    assert validated["publisher"][0]["transformation_note"] == (
        HAN_ORGANIZATION_NAME_TRANSFORMATION
    )


def test_han_organization_name_rejects_character_changes_and_expansion(session) -> None:
    cases = (
        (
            "中\n国银\n保监\n会消费者权益保护局",
            "中国银保监会消费者权益保护处",
        ),
        ("消\n保局", "中国银保监会消费者权益保护局"),
        (
            "中\n国银\n保监\n会消费者权益保护局甲",
            "中国银保监会消费者权益保护局",
        ),
    )

    for quote, target in cases:
        document = make_document(session, DataType.REGULATORY_CASE.value, quote)
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {"publisher": target},
                {
                    "publisher": evidence(
                        document,
                        quote,
                        mode="normalized",
                        note=HAN_ORGANIZATION_NAME_TRANSFORMATION,
                    )
                },
            )


def test_han_organization_name_rejects_punctuation_paragraphs_and_multiple_names(
    session,
) -> None:
    cases = (
        (
            "中国银保监会。\n消费者权益保护局",
            "中国银保监会消费者权益保护局",
        ),
        (
            "中国银行保险监督管理委员会\n国家金融监督管理总局",
            "中国银行保险监督管理委员会国家金融监督管理总局",
        ),
        (
            "中国银保监会\n\n消费者权益保护局",
            "中国银保监会消费者权益保护局",
        ),
    )

    for quote, target in cases:
        document = make_document(session, DataType.REGULATORY_CASE.value, quote)
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {"publisher": target},
                {
                    "publisher": evidence(
                        document,
                        quote,
                        mode="normalized",
                        note=HAN_ORGANIZATION_NAME_TRANSFORMATION,
                    )
                },
            )


def test_han_organization_name_transform_is_restricted_to_case_publisher(session) -> None:
    quote = "中\n国银\n保监\n会消费者权益保护局"
    target = "中国银保监会消费者权益保护局"
    cases = (
        (DataType.REGULATORY_CASE.value, "case_facts"),
        (DataType.PENALTY.value, "authority"),
        (DataType.REGULATION.value, "issuing_authority"),
        (DataType.PRODUCT_DOCUMENT.value, "company_name"),
    )

    for data_type, field_name in cases:
        document = make_document(session, data_type, quote)
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {field_name: target},
                {
                    field_name: evidence(
                        document,
                        quote,
                        mode="normalized",
                        note=HAN_ORGANIZATION_NAME_TRANSFORMATION,
                    )
                },
            )


def test_han_organization_name_requires_exact_versioned_normalized_mode(session) -> None:
    quote = "中\n国银\n保监\n会消费者权益保护局"
    target = "中国银保监会消费者权益保护局"
    document = make_document(session, DataType.REGULATORY_CASE.value, quote)
    with pytest.raises(FieldEvidenceError, match="evidence_offset_mismatch"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"publisher": target},
            {
                "publisher": [
                    {
                        "quote": target,
                        "page_number": 1,
                        "start_offset": 0,
                        "end_offset": len(target),
                        "mode": "normalized",
                        "transformation_note": HAN_ORGANIZATION_NAME_TRANSFORMATION,
                    }
                ]
            },
        )
    cases = (
        ("normalized", "whitespace_normalized"),
        ("normalized", f"{HAN_ORGANIZATION_NAME_TRANSFORMATION}_typo"),
        ("verbatim", HAN_ORGANIZATION_NAME_TRANSFORMATION),
    )

    for mode, note in cases:
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {"publisher": target},
                {
                    "publisher": evidence(
                        document,
                        quote,
                        mode=mode,
                        note=note,
                    )
                },
            )


def test_han_organization_name_enforces_han_whitespace_and_length_bounds(session) -> None:
    cases = (
        ("中国银保监会消费者权益保护局", "中国银保监会消费者权益保护局"),
        ("中国ABC\n监管局", "中国ABC监管局"),
        ("中国2026\n监管局", "中国2026监管局"),
        ("消\n保局", "消保局"),
        ("中\n" * 65 + "局", "中" * 65 + "局"),
        ("中国银保监会\u2029消费者权益保护局", "中国银保监会消费者权益保护局"),
        (
            "中\n国银\n保监\n会消费者权益保护局",
            "中国银保监会 消费者权益保护局",
        ),
    )

    for quote, target in cases:
        document = make_document(session, DataType.REGULATORY_CASE.value, quote)
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {"publisher": target},
                {
                    "publisher": evidence(
                        document,
                        quote,
                        mode="normalized",
                        note=HAN_ORGANIZATION_NAME_TRANSFORMATION,
                    )
                },
            )


def test_nfra_caption_is_narrowly_scoped_document_number_metadata(session) -> None:
    text = "保险销售行为管理办法"
    document = make_document(session, DataType.REGULATION.value, text)
    caption = "中国银行保险监督管理委员会令2022年第9号"
    ParsedArtifactService(settings_for(session)).persist(
        session,
        document,
        ParsedDocument(
            title=text,
            plain_text=text,
            pages=[ParsedPage(page_number=1, text=text)],
            metadata={"nfra": {"document_number": None, "caption": caption}},
        ),
        parser_name="NfraJsonParser",
    )
    session.commit()
    metadata_evidence = {
        "document_number": [
            {
                "quote": text,
                "page_number": 1,
                "start_offset": 0,
                "end_offset": len(text),
                "mode": "document_metadata",
                "metadata_field": "nfra.caption",
            }
        ]
    }

    FieldEvidenceService(settings_for(session)).validate(
        session,
        document,
        {"document_number": caption},
        metadata_evidence,
    )

    for forbidden_field in ("expected_title", "nfra.arbitrary"):
        rejected = json.loads(json.dumps(metadata_evidence))
        rejected["document_number"][0]["metadata_field"] = forbidden_field
        with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
            FieldEvidenceService(settings_for(session)).validate(
                session,
                document,
                {"document_number": caption},
                rejected,
            )

    forbidden_field_evidence = {
        "title": [
            {
                **metadata_evidence["document_number"][0],
                "metadata_field": "nfra.caption",
            }
        ]
    }
    with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"title": caption},
            forbidden_field_evidence,
        )


def test_nfra_caption_is_rejected_when_primary_document_number_exists(session) -> None:
    text = "保险销售行为管理办法"
    document = make_document(session, DataType.REGULATION.value, text)
    primary = "金规〔2026〕1号"
    caption = "中国银行保险监督管理委员会令2022年第9号"
    ParsedArtifactService(settings_for(session)).persist(
        session,
        document,
        ParsedDocument(
            title=text,
            plain_text=text,
            pages=[ParsedPage(page_number=1, text=text)],
            metadata={
                "nfra": {
                    "document_number": primary,
                    "caption": caption,
                }
            },
        ),
        parser_name="NfraJsonParser",
    )
    session.commit()

    with pytest.raises(FieldEvidenceError, match="field_not_supported_by_evidence"):
        FieldEvidenceService(settings_for(session)).validate(
            session,
            document,
            {"document_number": caption},
            {
                "document_number": [
                    {
                        "quote": text,
                        "page_number": 1,
                        "start_offset": 0,
                        "end_offset": len(text),
                        "mode": "document_metadata",
                        "metadata_field": "nfra.caption",
                    }
                ]
            },
        )


def test_summary_evidence_routes_demo_record_to_expert_review(session) -> None:
    document = make_document(
        session,
        DataType.PRODUCT_DOCUMENT.value,
        "真实原文",
        authenticity=AuthenticityType.DEMO_ONLY.value,
    )
    draft = envelope_for(document, "演示总结", "product_name", None)
    StructuredRecordService(settings_for(session)).import_draft(session, draft)

    result = ValidationService(settings_for(session)).validate_document(session, document)

    assert result.valid is False
    assert document.final_review_status == ReviewStatus.REQUIRES_EXPERT_REVIEW.value
    assert "summary_evidence_requires_expert_review" in {issue.code for issue in result.issues}


def make_reviewable_product(session) -> tuple[SourceDocument, ProductDocument, ReviewService]:
    document = make_document(
        session,
        DataType.PRODUCT_DOCUMENT.value,
        "旧产品 新产品",
        status=ReviewStatus.PENDING_REVIEW.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    product = ProductDocument(
        document_id=document.id,
        product_name="旧产品",
        source_quote="旧产品",
        field_evidence_json={"product_name": evidence(document, "旧产品")},
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()
    return document, product, ReviewService(settings_for(session))


def exported_review(
    session,
    service: ReviewService,
    data_type: str = DataType.PRODUCT_DOCUMENT.value,
) -> tuple[ReviewBatch, dict[str, object]]:
    batch = service.export_batch(session, data_type, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    return batch, row


def decision(
    batch: ReviewBatch,
    row: dict[str, object],
    *,
    status: str = ReviewStatus.APPROVED.value,
    corrections: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "batch_id": batch.id,
        "batch_item_id": row["batch_item_id"],
        "reviewed_payload_hash": payload_hash(row),
        "review_payload_schema_version": row["review_payload_schema_version"],
        "schema_version": batch.schema_version,
        "record_id": row["record_id"],
        "record_type": row["record_type"],
        "final_status": status,
        "field_reviews": {},
        "corrections": corrections or {},
        "evidence_quality": "A",
        "review_comment": "证据链测试",
        "reviewer": "tester",
    }


def test_review_correction_requires_matching_evidence(session) -> None:
    _, product, service = make_reviewable_product(session)
    batch, row = exported_review(session, service)
    corrections = {
        "records": [
            {
                "structured_record_id": product.id,
                "fields": {"product_name": "新产品"},
            }
        ]
    }

    with pytest.raises(ReviewDecisionError, match="correction_evidence_required"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections=corrections,
            ),
            batch.id,
        )


def test_review_correction_with_valid_evidence_is_atomic(session) -> None:
    document, product, service = make_reviewable_product(session)
    batch, row = exported_review(session, service)
    corrections = {
        "records": [
            {
                "structured_record_id": product.id,
                "fields": {"product_name": "新产品"},
                "field_evidence": {"product_name": evidence(document, "新产品")},
            }
        ]
    }

    service.apply_decision(
        session,
        decision(
            batch,
            row,
            status=ReviewStatus.APPROVED_WITH_REVISION.value,
            corrections=corrections,
        ),
        batch.id,
    )

    assert product.product_name == "新产品"
    assert product.field_evidence_json["product_name"][0]["quote"] == "新产品"
    assert document.metadata_json["post_review_validation"]["valid"] is True


def test_one_bad_evidence_rolls_back_all_record_corrections(session) -> None:
    text = "规则 第一条 原文甲。第二条 原文乙。修订甲 修订乙"
    document = make_document(
        session,
        DataType.REGULATION.value,
        text,
        status=ReviewStatus.PENDING_REVIEW.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    first = Regulation(
        document_id=document.id,
        title="规则",
        article_text="原文甲。",
        source_quote="原文甲。",
        field_evidence_json={
            "title": evidence(document, "规则"),
            "article_text": evidence(document, "原文甲。"),
        },
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    second = Regulation(
        document_id=document.id,
        title="规则",
        article_text="原文乙。",
        source_quote="原文乙。",
        field_evidence_json={
            "title": evidence(document, "规则"),
            "article_text": evidence(document, "原文乙。"),
        },
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add_all([first, second])
    session.commit()
    service = ReviewService(settings_for(session))
    batch, row = exported_review(session, service, DataType.REGULATION.value)
    bad = evidence(document, "修订乙")
    bad[0]["quote"] = "伪造乙"
    corrections = {
        "records": [
            {
                "structured_record_id": first.id,
                "fields": {"article_text": "修订甲"},
                "field_evidence": {"article_text": evidence(document, "修订甲")},
            },
            {
                "structured_record_id": second.id,
                "fields": {"article_text": "修订乙"},
                "field_evidence": {"article_text": bad},
            },
        ]
    }

    with pytest.raises(ReviewDecisionError, match="invalid_correction_value"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections=corrections,
            ),
            batch.id,
        )

    assert first.article_text == "原文甲。"
    assert second.article_text == "原文乙。"


@pytest.mark.parametrize("stage", ["validation", "export", "approval", "index"])
def test_database_raw_text_mutation_is_rejected_at_every_trust_gate(
    session,
    stage: str,
) -> None:
    if stage == "validation":
        document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "旧产品")
        session.add(
            ProductDocument(
                document_id=document.id,
                product_name="旧产品",
                source_quote="旧产品",
                field_evidence_json={"product_name": evidence(document, "旧产品")},
                final_review_status=ReviewStatus.PARSED.value,
            )
        )
        session.commit()
        document.raw_text = "数据库被修改"
        session.commit()
        with pytest.raises(ParsedArtifactIntegrityError, match="stored_raw_text_mismatch"):
            ValidationService(settings_for(session)).validate_document(session, document)
        return

    document, _, service = make_reviewable_product(session)
    if stage == "export":
        document.raw_text = "数据库被修改"
        session.commit()
        with pytest.raises(ParsedArtifactIntegrityError, match="stored_raw_text_mismatch"):
            service.export_batch(session, DataType.PRODUCT_DOCUMENT.value, "jsonl")
        return

    batch, row = exported_review(session, service)
    if stage == "approval":
        document.raw_text = "数据库被修改"
        session.commit()
        with pytest.raises(ParsedArtifactIntegrityError, match="stored_raw_text_mismatch"):
            service.apply_decision(session, decision(batch, row), batch.id)
        return

    service.apply_decision(session, decision(batch, row), batch.id)
    document.raw_text = "数据库被修改"
    session.commit()
    summary = KnowledgeIndexService(settings_for(session)).index_approved(session)
    assert "stored_raw_text_mismatch" in summary.rejected[document.id]


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("modified", "parsed_artifact_hash_mismatch"),
        ("deleted", "parsed_artifact_missing"),
        ("raw_binding", "parsed_raw_hash_mismatch"),
        ("text_hash", "parsed_text_hash_mismatch"),
        ("outside", "parsed_artifact_path_outside_storage"),
        ("chunk", "document_chunk_mismatch"),
    ],
)
def test_parsed_artifact_integrity_mutations_are_rejected(
    session,
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "不可变解析文本")
    artifact_path = Path(document.parsed_artifact_path or "")
    if mutation == "modified":
        artifact_path.write_text("{}", encoding="utf-8")
    elif mutation == "deleted":
        artifact_path.unlink()
    elif mutation == "raw_binding":
        document.parsed_from_raw_sha256 = "0" * 64
        session.commit()
    elif mutation == "text_hash":
        document.parsed_text_sha256 = "0" * 64
        session.commit()
    elif mutation == "outside":
        outside = tmp_path / "outside-parsed.json"
        shutil.copyfile(artifact_path, outside)
        document.parsed_artifact_path = str(outside)
        session.commit()
    else:
        document.chunks[0].text = "篡改分块"
        session.commit()

    with pytest.raises(ParsedArtifactIntegrityError, match=error):
        ParsedArtifactIntegrityService(settings_for(session)).verify(
            document,
            session=session,
        )


def make_failed_draft(
    session,
    data_type: str,
) -> tuple[SourceDocument, ProductDocument | Penalty]:
    if data_type == DataType.PRODUCT_DOCUMENT.value:
        text, old_value = "旧产品 新产品", "旧产品"
        document = make_document(
            session,
            data_type,
            text,
            status=ReviewStatus.AUTO_VALIDATION_FAILED.value,
        )
        record: ProductDocument | Penalty = ProductDocument(
            document_id=document.id,
            product_name=old_value,
            source_quote=old_value,
            field_evidence_json={"product_name": evidence(document, old_value)},
            final_review_status=ReviewStatus.AUTO_VALIDATION_FAILED.value,
        )
    else:
        text, old_value = "旧违法事实 新违法事实", "旧违法事实"
        document = make_document(
            session,
            data_type,
            text,
            status=ReviewStatus.AUTO_VALIDATION_FAILED.value,
        )
        record = Penalty(
            document_id=document.id,
            source_entry_index=1,
            source_entry_fingerprint="4" * 64,
            illegal_facts=old_value,
            original_sales_wording_disclosed=False,
            source_quote=old_value,
            field_evidence_json={"illegal_facts": evidence(document, old_value)},
            final_review_status=ReviewStatus.AUTO_VALIDATION_FAILED.value,
        )
    document.metadata_json = {"automatic_validation": {"valid": False, "issues": ["old"]}}
    session.add(record)
    session.commit()
    return document, record


@pytest.mark.parametrize(
    ("data_type", "field_name", "new_value"),
    [
        (DataType.PRODUCT_DOCUMENT.value, "product_name", "新产品"),
        (DataType.PENALTY.value, "illegal_facts", "新违法事实"),
    ],
)
def test_auto_validation_failed_primary_draft_can_be_revised(
    session,
    data_type: str,
    field_name: str,
    new_value: str,
) -> None:
    document, record = make_failed_draft(session, data_type)
    envelope = StructuredDraftRevisionEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": data_type,
            "structured_record_id": record.id,
            "fields": {field_name: new_value},
            "field_evidence": {field_name: evidence(document, new_value)},
        }
    )

    StructuredDraftRevisionService(settings_for(session)).revise(
        session,
        envelope,
        reason="修正错误字段",
        actor="tester",
    )

    assert getattr(record, field_name) == new_value
    assert document.final_review_status == ReviewStatus.PARSED.value
    assert "automatic_validation" not in document.metadata_json
    assert session.query(StructuredDraftRevision).filter_by(document_id=document.id).count() == 1


def test_revised_draft_must_pass_validation_before_review_export(session) -> None:
    document, record = make_failed_draft(session, DataType.PRODUCT_DOCUMENT.value)
    revision = StructuredDraftRevisionEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": document.data_type,
            "structured_record_id": record.id,
            "fields": {"product_name": "新产品"},
            "field_evidence": {"product_name": evidence(document, "新产品")},
        }
    )
    StructuredDraftRevisionService(settings_for(session)).revise(
        session,
        revision,
        reason="修正",
        actor="tester",
    )
    service = ReviewService(settings_for(session))

    with pytest.raises(ReviewDecisionError, match="no_records_available_for_review"):
        service.export_batch(session, document.data_type, "jsonl")

    result = ValidationService(settings_for(session)).validate_document(session, document)
    assert result.valid is True
    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value


def test_pending_review_draft_cannot_be_revised(session) -> None:
    document, record, _ = make_reviewable_product(session)
    revision = StructuredDraftRevisionEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": document.data_type,
            "structured_record_id": record.id,
            "fields": {"product_name": "新产品"},
            "field_evidence": {"product_name": evidence(document, "新产品")},
        }
    )

    with pytest.raises(StructuredRecordError, match="structured_record_locked"):
        StructuredDraftRevisionService(settings_for(session)).revise(
            session,
            revision,
            reason="越权修订",
            actor="tester",
        )


def test_same_record_cannot_enter_two_open_batches(session) -> None:
    _, _, service = make_reviewable_product(session)
    first, _ = exported_review(session, service)

    with pytest.raises(ReviewDecisionError, match="no_records_available_for_review"):
        service.export_batch(session, DataType.PRODUCT_DOCUMENT.value, "jsonl")

    assert first.status == "exported"
    assert session.query(ReviewBatch).count() == 1


def test_database_unique_constraint_serializes_competing_reservations(session) -> None:
    document, _, service = make_reviewable_product(session)
    first, _ = exported_review(session, service)
    second = ReviewBatch(
        batch_name="competing",
        data_type=document.data_type,
        record_count=1,
        export_path="/tmp/competing.jsonl",
        status="exported",
    )
    session.add(second)
    session.flush()
    session.add(
        ReviewReservation(
            record_type=document.data_type,
            record_id=document.id,
            document_id=document.id,
            batch_id=second.id,
            status="active",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert first.status == "exported"


def test_cancelled_batch_releases_record_for_reexport(session) -> None:
    _, _, service = make_reviewable_product(session)
    first, _ = exported_review(session, service)

    service.cancel_batch(session, first.id, "重新分配")
    second, _ = exported_review(session, service)

    assert first.status == "cancelled"
    assert first.cancellation_reason == "重新分配"
    assert second.id != first.id


def test_completed_batch_cannot_be_cancelled(session) -> None:
    _, _, service = make_reviewable_product(session)
    batch, row = exported_review(session, service)
    service.apply_decision(session, decision(batch, row), batch.id)

    with pytest.raises(ReviewDecisionError, match="review_batch_not_cancellable"):
        service.cancel_batch(session, batch.id, "不得取消")


def test_portable_bundle_contains_parsed_artifact_and_field_evidence(session) -> None:
    document, _, service = make_reviewable_product(session)
    batch, bundle_path = service.export_bundle(session, document.data_type)

    with zipfile.ZipFile(bundle_path) as bundle:
        row = json.loads(bundle.read("review.jsonl"))
        manifest = json.loads(bundle.read("manifest.json"))
        assert row["parsed_artifact_path"] in bundle.namelist()
        assert (
            row["parsed_fields"]["records"][0]["field_evidence"]["product_name"][0]["quote"]
            == "旧产品"
        )
        item = manifest["items"][0]
        assert item["raw_sha256"] == document.sha256
        assert item["parsed_artifact_sha256"] == document.parsed_artifact_sha256
        assert item["parsed_text_sha256"] == document.parsed_text_sha256
        assert item["field_evidence_summary"]
    assert batch.bundle_path == str(bundle_path)


def test_tampered_bundle_parsed_artifact_rejects_decision(session) -> None:
    document, _, service = make_reviewable_product(session)
    batch, bundle_path = service.export_bundle(session, document.data_type)
    with zipfile.ZipFile(bundle_path) as source:
        row = json.loads(source.read("review.jsonl"))
        members = {name: source.read(name) for name in source.namelist()}
    members[row["parsed_artifact_path"]] = b"tampered"
    with zipfile.ZipFile(bundle_path, "w") as target:
        for name, content in members.items():
            target.writestr(name, content)

    with pytest.raises(ReviewDecisionError, match="review_package_tampered"):
        service.apply_decision(session, decision(batch, row), batch.id)


def test_regulation_add_and_delete_are_audited_pre_review(session) -> None:
    text = "规则 第一条 新增条款"
    document = make_document(session, DataType.REGULATION.value, text)
    service = StructuredDraftRevisionService(settings_for(session))
    addition = StructuredDraftRevisionEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": document.data_type,
            "action": "add",
            "fields": {
                "title": "规则",
                "article_number": "第一条",
                "article_text": "新增条款",
                "source_quote": "新增条款",
            },
            "field_evidence": {
                "title": evidence(document, "规则"),
                "article_number": evidence(document, "第一条"),
                "article_text": evidence(document, "新增条款"),
            },
        }
    )
    record = service.revise(session, addition, reason="补录条款", actor="tester")
    deletion = StructuredDraftRevisionEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": document.data_type,
            "structured_record_id": record.id,
            "action": "delete",
        }
    )

    service.revise(session, deletion, reason="删除误录", actor="tester")

    assert session.query(Regulation).filter_by(document_id=document.id).count() == 0
    assert [
        row.action
        for row in session.query(StructuredDraftRevision)
        .filter_by(document_id=document.id)
        .order_by(StructuredDraftRevision.id)
    ] == ["add", "delete"]


def test_reparse_preserves_history_and_invalidates_structured_draft(session) -> None:
    document = make_document(session, DataType.PRODUCT_DOCUMENT.value, "旧解析")
    session.add(
        ProductDocument(
            document_id=document.id,
            product_name="旧解析",
            source_quote="旧解析",
            field_evidence_json={"product_name": evidence(document, "旧解析")},
            final_review_status=ReviewStatus.PARSED.value,
        )
    )
    session.commit()
    previous_artifact = document.parsed_artifact_sha256

    ParsingService(settings_for(session)).reparse_document(session, document, "解析器升级")

    assert session.query(ProductDocument).filter_by(document_id=document.id).count() == 0
    assert session.query(ParsedArtifactVersion).filter_by(document_id=document.id).count() == 2
    assert document.parsed_artifact_sha256 != previous_artifact
    assert document.final_review_status == ReviewStatus.PARSED.value


def test_empty_export_does_not_create_batch(session) -> None:
    service = ReviewService(settings_for(session))

    with pytest.raises(ReviewDecisionError, match="no_records_available_for_review"):
        service.export_batch(session, DataType.PRODUCT_DOCUMENT.value, "jsonl")

    assert session.query(ReviewBatch).count() == 0


def test_failed_index_keeps_document_not_indexed(session) -> None:
    document = make_document(
        session,
        DataType.PRODUCT_DOCUMENT.value,
        "旧产品",
        status=ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    document.raw_text = "数据库被修改"
    session.commit()

    KnowledgeIndexService(settings_for(session)).index_approved(session)

    assert document.knowledge_index_status == KnowledgeIndexStatus.NOT_INDEXED.value


def regulation_draft(
    document: SourceDocument,
    *,
    title: str,
    title_evidence: list[dict[str, object]] | None,
    validity_status: str | None = "unknown",
) -> StructuredDraftEnvelope:
    payload: dict[str, list[dict[str, object]]] = {"article_text": evidence(document, "监管条款")}
    if title_evidence is not None:
        payload["title"] = title_evidence
    return StructuredDraftEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": DataType.REGULATION.value,
            "fields": {
                "title": title,
                "article_text": "监管条款",
                "source_quote": "监管条款",
                "validity_status": validity_status,
            },
            "field_evidence": payload,
        }
    )


def penalty_draft(
    document: SourceDocument,
    *,
    punished_entity: str,
    entity_evidence: list[dict[str, object]] | None,
) -> StructuredDraftEnvelope:
    payload: dict[str, list[dict[str, object]]] = {"illegal_facts": evidence(document, "违法事实")}
    if entity_evidence is not None:
        payload["punished_entity"] = entity_evidence
    locator = {"table_index": 1, "logical_row": 1}
    fields = {
        "punished_entity": punished_entity,
        "illegal_facts": "违法事实",
    }
    identity_evidence = dict(payload)
    if entity_evidence is None:
        identity_evidence["punished_entity"] = evidence(document, "违法事实")
    if entity_evidence is None:
        fragments = [
            {
                "quote": "违法事实",
                "start_offset": 0,
                "end_offset": len("违法事实"),
            }
        ]
        content_sha256 = "a" * 64
    else:
        fragments = build_penalty_source_entry_fragments(fields, identity_evidence)
        content_sha256 = source_entry_content_sha256(
            build_penalty_identity_material(fields, identity_evidence)
        )
    return StructuredDraftEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": DataType.PENALTY.value,
            "source_entry_locator": locator,
            "source_entry_fragments": fragments,
            "source_entry_content_sha256": content_sha256,
            "fields": {
                "source_entry_index": 1,
                "source_entry_fingerprint": penalty_source_entry_fingerprint(
                    raw_artifact_sha256=document.sha256,
                    source_entry_content_sha256=content_sha256,
                ),
                "punished_entity": punished_entity,
                "illegal_facts": "违法事实",
                "original_sales_wording_disclosed": False,
                "original_sales_wording": None,
                "source_quote": "违法事实",
            },
            "field_evidence": payload,
        }
    )


def test_fabricated_regulation_title_without_evidence_is_rejected(session) -> None:
    document = make_document(session, DataType.REGULATION.value, "监管条款")

    with pytest.raises(StructuredRecordError, match="missing_field_evidence"):
        StructuredRecordService(settings_for(session)).import_draft(
            session,
            regulation_draft(
                document,
                title="虚构监管标题",
                title_evidence=None,
            ),
        )


def test_regulation_title_supported_by_body_evidence_is_accepted(session) -> None:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "保险销售行为管理办法 监管条款",
    )

    record = StructuredRecordService(settings_for(session)).import_draft(
        session,
        regulation_draft(
            document,
            title="保险销售行为管理办法",
            title_evidence=evidence(document, "保险销售行为管理办法"),
        ),
    )

    assert record.title == "保险销售行为管理办法"
    assert record.validity_status == "unknown"


def test_regulation_title_supported_by_source_title_metadata_is_accepted(session) -> None:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "保险销售行为管理办法 监管条款",
    )
    document.source_title = "保险销售行为管理办法"
    session.commit()
    metadata_evidence = evidence(document, "保险销售行为管理办法")
    metadata_evidence[0]["mode"] = "document_metadata"
    metadata_evidence[0]["metadata_field"] = "source_title"

    record = StructuredRecordService(settings_for(session)).import_draft(
        session,
        regulation_draft(
            document,
            title="保险销售行为管理办法",
            title_evidence=metadata_evidence,
        ),
    )

    assert record.field_evidence_json["title"][0]["metadata_field"] == "source_title"


def make_reviewable_regulation_record(
    session,
) -> tuple[SourceDocument, Regulation, ReviewService]:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "旧标题 新标题 监管条款",
        status=ReviewStatus.PENDING_REVIEW.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    record = Regulation(
        document_id=document.id,
        title="旧标题",
        validity_status="unknown",
        article_text="监管条款",
        source_quote="监管条款",
        field_evidence_json={
            "title": evidence(document, "旧标题"),
            "article_text": evidence(document, "监管条款"),
        },
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(record)
    session.commit()
    return document, record, ReviewService(settings_for(session))


def test_title_correction_without_new_evidence_is_rejected(session) -> None:
    _, record, service = make_reviewable_regulation_record(session)
    batch, row = exported_review(session, service, DataType.REGULATION.value)

    with pytest.raises(ReviewDecisionError, match="correction_evidence_required"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": record.id,
                            "fields": {"title": "新标题"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_title_correction_cannot_reuse_unsupported_old_evidence(session) -> None:
    document, record, service = make_reviewable_regulation_record(session)
    batch, row = exported_review(session, service, DataType.REGULATION.value)

    with pytest.raises(ReviewDecisionError, match="invalid_correction_value"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": record.id,
                            "fields": {"title": "新标题"},
                            "field_evidence": {"title": evidence(document, "旧标题")},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_fabricated_punished_entity_without_evidence_is_rejected(session) -> None:
    document = make_document(session, DataType.PENALTY.value, "违法事实")

    with pytest.raises(StructuredRecordError, match="missing_field_evidence"):
        StructuredRecordService(settings_for(session)).import_draft(
            session,
            penalty_draft(
                document,
                punished_entity="虚构保险公司",
                entity_evidence=None,
            ),
        )


def test_punished_entity_supported_by_body_evidence_is_accepted(session) -> None:
    document = make_document(
        session,
        DataType.PENALTY.value,
        "某某人寿保险股份有限公司 违法事实",
    )

    record = StructuredRecordService(settings_for(session)).import_draft(
        session,
        penalty_draft(
            document,
            punished_entity="某某人寿保险股份有限公司",
            entity_evidence=evidence(document, "某某人寿保险股份有限公司"),
        ),
    )

    assert record.punished_entity == "某某人寿保险股份有限公司"


def test_punished_entity_correction_without_new_evidence_is_rejected(session) -> None:
    document = make_document(
        session,
        DataType.PENALTY.value,
        "旧机构 新机构 违法事实",
        status=ReviewStatus.PENDING_REVIEW.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    record = Penalty(
        document_id=document.id,
        source_entry_index=1,
        source_entry_fingerprint="5" * 64,
        punished_entity="旧机构",
        illegal_facts="违法事实",
        original_sales_wording_disclosed=False,
        source_quote="违法事实",
        field_evidence_json={
            "punished_entity": evidence(document, "旧机构"),
            "illegal_facts": evidence(document, "违法事实"),
        },
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(record)
    session.commit()
    service = ReviewService(settings_for(session))
    batch, row = exported_review(session, service, DataType.PENALTY.value)

    with pytest.raises(ReviewDecisionError, match="correction_evidence_required"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": record.id,
                            "fields": {"punished_entity": "新机构"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_arbitrary_regulation_validity_status_is_rejected_by_schema() -> None:
    with pytest.raises(PydanticValidationError):
        RegulationDraft(
            title="规则",
            article_text="条款",
            source_quote="条款",
            validity_status="现行有效（虚构）",
        )


def test_ordinary_draft_cannot_set_effective_status(session) -> None:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "保险销售行为管理办法 监管条款",
    )
    payload = {
        "document_id": document.id,
        "record_type": DataType.REGULATION.value,
        "fields": {
            "title": "保险销售行为管理办法",
            "article_text": "监管条款",
            "source_quote": "监管条款",
            "validity_status": "effective",
        },
        "field_evidence": {
            "title": evidence(document, "保险销售行为管理办法"),
            "article_text": evidence(document, "监管条款"),
        },
    }

    with pytest.raises(StructuredRecordError):
        StructuredRecordService(settings_for(session)).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_ordinary_correction_cannot_set_effective_status(session) -> None:
    _, record, service = make_reviewable_regulation_record(session)
    batch, row = exported_review(session, service, DataType.REGULATION.value)

    with pytest.raises(ReviewDecisionError, match="Unknown correction field"):
        service.apply_decision(
            session,
            decision(
                batch,
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": record.id,
                            "fields": {"validity_status": "effective"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_unknown_regulation_validity_status_is_saved(session) -> None:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "保险销售行为管理办法 监管条款",
    )

    record = StructuredRecordService(settings_for(session)).import_draft(
        session,
        regulation_draft(
            document,
            title="保险销售行为管理办法",
            title_evidence=evidence(document, "保险销售行为管理办法"),
        ),
    )

    assert record.validity_status == "unknown"


def test_database_rejects_non_unknown_regulation_validity_status(session) -> None:
    document = make_document(session, DataType.REGULATION.value, "规则 监管条款")
    session.add(
        Regulation(
            document_id=document.id,
            title="规则",
            validity_status="effective",
            article_text="监管条款",
            source_quote="监管条款",
            final_review_status=ReviewStatus.PARSED.value,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_regulation_record_response_marks_unknown_validity_as_pending(session) -> None:
    document = make_document(session, DataType.REGULATION.value, "规则 监管条款")
    session.add(
        Regulation(
            document_id=document.id,
            title="规则",
            validity_status="unknown",
            article_text="监管条款",
            source_quote="监管条款",
            field_evidence_json={
                "title": evidence(document, "规则"),
                "article_text": evidence(document, "监管条款"),
            },
            final_review_status=ReviewStatus.PARSED.value,
        )
    )
    session.commit()
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).get(f"/records/regulation/{document.id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["regulation_validity_status"] == "unknown"
    assert response.json()["regulation_validity_display"] == "效力状态待核验"


def test_unknown_validity_regulation_remains_eligible_for_knowledge_index(session) -> None:
    document = make_document(
        session,
        DataType.REGULATION.value,
        "规则 监管条款",
        status=ReviewStatus.APPROVED.value,
        authenticity=AuthenticityType.VERIFIED_PUBLIC.value,
    )
    document.metadata_json = {"automatic_validation": {"valid": True, "issues": []}}
    session.add(
        Regulation(
            document_id=document.id,
            title="规则",
            validity_status="unknown",
            article_text="监管条款",
            source_quote="监管条款",
            field_evidence_json={
                "title": evidence(document, "规则"),
                "article_text": evidence(document, "监管条款"),
            },
            final_review_status=ReviewStatus.APPROVED.value,
        )
    )
    session.commit()

    summary = KnowledgeIndexService(settings_for(session)).index_approved(session)

    assert summary.indexed == 1
    assert document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
