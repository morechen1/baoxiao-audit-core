from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.exceptions import StructuredRecordError
from app.models import Penalty, ReviewBatch, ReviewDecision, SourceDocument
from app.models.enums import (
    AuthenticityType,
    DataType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.schemas.structured import (
    StructuredDraftEnvelope,
    StructuredDraftRevisionEnvelope,
)
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.penalty_entries import (
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
)
from app.services.review import ReviewService
from app.services.review.service import payload_hash
from app.services.review_payload import portable_record_key
from app.services.structured_records import StructuredRecordService
from app.services.structured_revisions import StructuredDraftRevisionService
from app.services.validation import ValidationService
from migrations.versions import b9c8d7e6f5a4_support_multi_record_penalties as migration


def make_document(
    session,
    text: str = "违法事实一\n违法事实二\n修订事实一",
    *,
    source_url: str = "https://example.test/penalty",
    nfra_doc_id: str | None = None,
    parsed_metadata: dict[str, object] | None = None,
) -> SourceDocument:
    data_dir = session.info["data_dir"]
    raw_path = data_dir / "raw" / f"{hashlib.sha256(source_url.encode()).hexdigest()}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")
    document = SourceDocument(
        data_type=DataType.PENALTY.value,
        source_url=source_url,
        raw_file_path=str(raw_path),
        raw_text=text,
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PARSED.value,
    )
    session.add(document)
    session.flush()
    ParsedArtifactService(Settings(data_dir=data_dir)).persist(
        session,
        document,
        ParsedDocument(
            title="处罚测试",
            plain_text=text,
            pages=[ParsedPage(page_number=1, text=text)],
            metadata=(
                parsed_metadata
                if parsed_metadata is not None
                else (
                    {"nfra": {"doc_id": nfra_doc_id}, "source_format": "nfra_public_json"}
                    if nfra_doc_id
                    else {}
                )
            ),
        ),
        parser_name="TestParser",
    )
    session.commit()
    return document


def envelope(
    document: SourceDocument,
    index: int,
    text: str,
    *,
    locator: dict[str, object] | None = None,
) -> StructuredDraftEnvelope:
    stable_locator = locator or {"table_index": 1, "logical_row": index}
    start = (document.raw_text or "").index(text)
    fields = {
        "source_entry_index": index,
        "punished_entity": text,
        "illegal_facts": text,
        "original_sales_wording_disclosed": False,
        "source_quote": text,
    }
    evidence_item = {
        "quote": text,
        "page_number": 1,
        "start_offset": start,
        "end_offset": start + len(text),
        "mode": "verbatim",
    }
    field_evidence = {
        "punished_entity": [evidence_item],
        "illegal_facts": [evidence_item],
    }
    fragments = build_penalty_source_entry_fragments(fields, field_evidence)
    content_sha256 = source_entry_content_sha256(
        build_penalty_identity_material(fields, field_evidence)
    )
    fingerprint = penalty_source_entry_fingerprint(
        raw_artifact_sha256=document.sha256,
        source_entry_content_sha256=content_sha256,
    )
    return StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        source_entry_locator=stable_locator,
        source_entry_fragments=fragments,
        source_entry_content_sha256=content_sha256,
        fields={**fields, "source_entry_fingerprint": fingerprint},
        field_evidence=field_evidence,
    )


def service(session) -> StructuredRecordService:
    return StructuredRecordService(Settings(data_dir=session.info["data_dir"]))


def complete_envelope(
    document: SourceDocument,
    *,
    index: int = 1,
    locator: dict[str, object] | None = None,
) -> StructuredDraftEnvelope:
    entity = "某保险公司"
    result = "罚款10万元"
    fact = "销售误导"
    evidence = {}
    for field_name, quote in (
        ("punished_entity", entity),
        ("penalty_result", result),
    ):
        start = (document.raw_text or "").index(quote)
        evidence[field_name] = [
            {
                "quote": quote,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(quote),
                "mode": "verbatim",
            }
        ]
    fact_start = (document.raw_text or "").index(fact)
    evidence["illegal_facts"] = [
        {
            "quote": fact,
            "page_number": 1,
            "start_offset": fact_start,
            "end_offset": fact_start + len(fact),
            "mode": "verbatim",
        }
    ]
    fields = {
        "source_entry_index": index,
        "punished_entity": entity,
        "illegal_facts": fact,
        "penalty_result": result,
        "original_sales_wording_disclosed": False,
        "source_quote": fact,
    }
    fragments = build_penalty_source_entry_fragments(fields, evidence)
    content_sha256 = source_entry_content_sha256(build_penalty_identity_material(fields, evidence))
    return StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        source_entry_locator=locator or {"table_index": 1, "logical_row": index},
        source_entry_fragments=fragments,
        source_entry_content_sha256=content_sha256,
        fields={
            **fields,
            "source_entry_fingerprint": penalty_source_entry_fingerprint(
                raw_artifact_sha256=document.sha256,
                source_entry_content_sha256=content_sha256,
            ),
        },
        field_evidence=evidence,
    )


def test_penalty_fingerprint_is_stable_and_has_fixed_serialization() -> None:
    inputs = {
        "raw_artifact_sha256": "a" * 64,
        "source_entry_content_sha256": "b" * 64,
    }

    first = penalty_source_entry_fingerprint(**inputs)
    second = penalty_source_entry_fingerprint(**inputs)

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("field", "replacement"),
    [("raw_artifact_sha256", "c" * 64), ("source_entry_content_sha256", "d" * 64)],
)
def test_penalty_fingerprint_binds_every_material_input(
    field: str,
    replacement: object,
) -> None:
    inputs = {
        "raw_artifact_sha256": "a" * 64,
        "source_entry_content_sha256": "b" * 64,
    }
    baseline = penalty_source_entry_fingerprint(**inputs)

    assert penalty_source_entry_fingerprint(**{**inputs, field: replacement}) != baseline


def test_one_document_imports_multiple_penalties_with_distinct_identities(session) -> None:
    document = make_document(session)

    first = service(session).import_draft(session, envelope(document, 1, "违法事实一"))
    second = service(session).import_draft(session, envelope(document, 2, "违法事实二"))

    assert first.id != second.id
    assert first.source_entry_index == 1
    assert second.source_entry_index == 2
    assert first.source_entry_fingerprint != second.source_entry_fingerprint
    assert session.query(Penalty).filter_by(document_id=document.id).count() == 2


def test_duplicate_entry_index_rolls_back_whole_jsonl_batch(session, tmp_path: Path) -> None:
    document = make_document(session)
    first = envelope(document, 1, "违法事实一")
    duplicate = envelope(
        document,
        1,
        "违法事实二",
        locator={"table_index": 1, "logical_row": 2},
    )
    path = tmp_path / "duplicate-index.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
            for item in (first, duplicate)
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = service(session).import_jsonl(session, path)

    assert imported == 0
    assert "penalty_source_entry_duplicate" in errors[0]
    assert session.query(Penalty).filter_by(document_id=document.id).count() == 0


def test_fingerprint_mismatch_rolls_back_whole_jsonl_batch(session, tmp_path: Path) -> None:
    document = make_document(session)
    first = envelope(document, 1, "违法事实一")
    invalid_payload = envelope(document, 2, "违法事实二").model_dump(mode="json")
    invalid_payload["fields"]["source_entry_fingerprint"] = first.fields["source_entry_fingerprint"]
    path = tmp_path / "duplicate-fingerprint.jsonl"
    path.write_text(
        "\n".join(
            (
                json.dumps(first.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(invalid_payload, ensure_ascii=False),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = service(session).import_jsonl(session, path)

    assert imported == 0
    assert "penalty_source_entry_fingerprint_mismatch" in errors[0]
    assert session.query(Penalty).filter_by(document_id=document.id).count() == 0


def test_database_rejects_duplicate_index_and_fingerprint_per_document(session) -> None:
    document = make_document(session)
    first = service(session).import_draft(session, envelope(document, 1, "违法事实一"))
    session.add(
        Penalty(
            document_id=document.id,
            source_entry_index=1,
            source_entry_fingerprint=first.source_entry_fingerprint,
            illegal_facts="违法事实二",
            original_sales_wording_disclosed=False,
            source_quote="违法事实二",
            final_review_status=ReviewStatus.PARSED.value,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_review_rows_sort_penalties_by_entry_index_and_keys_are_distinct(session) -> None:
    document = make_document(session)
    record_two = service(session).import_draft(session, envelope(document, 2, "违法事实二"))
    record_one = service(session).import_draft(session, envelope(document, 1, "违法事实一"))

    row = ReviewService(Settings(data_dir=session.info["data_dir"]))._document_review_row(
        session, document, 1, 1
    )
    records = row["parsed_fields"]["records"]

    assert [record["source_entry_index"] for record in records] == [1, 2]
    assert records[0]["structured_record_id"] == record_one.id
    assert records[1]["structured_record_id"] == record_two.id
    assert len({record["portable_record_key"] for record in records}) == 2


def test_penalty_portable_key_is_stable_across_database_ids() -> None:
    record = {
        "structured_record_id": 10,
        "source_entry_index": 3,
        "source_entry_fingerprint": "b" * 64,
        "illegal_facts": "违法事实",
        "source_quote": "违法事实",
        "field_evidence": {},
        "draft_provenance": None,
    }
    changed = {**record, "structured_record_id": 999}

    assert portable_record_key("a" * 64, "penalty", record) == portable_record_key(
        "a" * 64,
        "penalty",
        changed,
    )


def test_revision_targets_only_one_penalty_record(session) -> None:
    document = make_document(session)
    first = service(session).import_draft(session, envelope(document, 1, "违法事实一"))
    second = service(session).import_draft(session, envelope(document, 2, "违法事实二"))
    start = (document.raw_text or "").index("修订事实一")
    revision = StructuredDraftRevisionEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        structured_record_id=first.id,
        fields={"illegal_facts": "修订事实一", "source_quote": "修订事实一"},
        field_evidence={
            "illegal_facts": [
                {
                    "quote": "修订事实一",
                    "page_number": 1,
                    "start_offset": start,
                    "end_offset": start + len("修订事实一"),
                    "mode": "verbatim",
                }
            ]
        },
    )

    StructuredDraftRevisionService(Settings(data_dir=session.info["data_dir"])).revise(
        session, revision, reason="修正单条记录", actor="tester"
    )

    assert first.illegal_facts == "修订事实一"
    assert second.illegal_facts == "违法事实二"


def test_cross_document_exact_duplicate_is_marked_but_not_deleted(session) -> None:
    first_document = make_document(session, source_url="https://example.test/a")
    second_document = make_document(
        session,
        text="违法事实一\n违法事实二\n修订事实一\n另一原件",
        source_url="https://example.test/b",
    )
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, "违法事实一"),
    )
    second = service(session).import_draft(
        session,
        envelope(second_document, 1, "违法事实一"),
    )

    assert first.duplicate_candidate is True
    assert second.duplicate_candidate is True
    assert session.query(Penalty).count() == 2


def test_cross_document_high_similarity_is_only_marked_for_human_resolution(session) -> None:
    first_text = "某保险销售人员在产品介绍过程中连续多次使用误导性宣传表述"
    second_text = "某保险销售人员在产品介绍过程中连续多次使用误导性宣传措辞"
    first_document = make_document(
        session,
        text=first_text,
        source_url="https://example.test/similar-a",
    )
    second_document = make_document(
        session,
        text=second_text,
        source_url="https://example.test/similar-b",
    )
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, first_text),
    )
    second = service(session).import_draft(
        session,
        envelope(second_document, 1, second_text),
    )

    assert first.duplicate_candidate is True
    assert second.duplicate_candidate is True
    assert session.query(Penalty).count() == 2


def test_penalty_validation_requires_expert_review_and_never_auto_indexes(session) -> None:
    document = make_document(session)
    service(session).import_draft(session, envelope(document, 1, "违法事实一"))

    result = ValidationService(Settings(data_dir=session.info["data_dir"])).validate_document(
        session, document
    )

    assert result.valid is True
    assert document.final_review_status == ReviewStatus.REQUIRES_EXPERT_REVIEW.value
    assert document.knowledge_index_status == KnowledgeIndexStatus.NOT_INDEXED.value


def test_migration_downgrade_fails_closed_for_multi_record_document(monkeypatch) -> None:
    class Result:
        @staticmethod
        def scalar_one_or_none() -> int:
            return 42

    class Connection:
        @staticmethod
        def execute(statement):
            return Result()

    monkeypatch.setattr(migration.op, "get_bind", lambda: Connection())

    with pytest.raises(RuntimeError, match="cannot_downgrade_multi_record_penalties"):
        migration.downgrade()


def test_legacy_migration_fingerprint_is_deterministic() -> None:
    first = migration._legacy_fingerprint("a" * 64, "不可变旧记录")
    second = migration._legacy_fingerprint("a" * 64, "不可变旧记录")

    assert first == second
    assert len(first) == 64


def test_same_fragments_ignore_index_and_locator_and_duplicate_is_rejected(session) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    first = complete_envelope(document, index=1)
    second = complete_envelope(
        document,
        index=2,
        locator={"table_index": 9, "numbered_entry": 7},
    )

    assert first.fields["source_entry_fingerprint"] == second.fields["source_entry_fingerprint"]
    service(session).import_draft(session, first)
    with pytest.raises(StructuredRecordError, match="penalty_source_entry_duplicate"):
        service(session).import_draft(session, second)


@pytest.mark.parametrize("mutation", ["wrong_offset", "duplicate"])
def test_invalid_source_fragments_fail_closed(session, mutation: str) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    payload = complete_envelope(document).model_dump(mode="json")
    fragments = payload["source_entry_fragments"]
    if mutation == "wrong_offset":
        fragments[0]["start_offset"] += 1
    else:
        fragments.append(dict(fragments[0]))
    candidate = StructuredDraftEnvelope.model_validate(payload)

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_fragment_invalid",
    ):
        service(session).import_draft(session, candidate)


def test_overlapping_fixed_field_evidence_is_accepted(session) -> None:
    text = "某保险公司销售误导，被罚款10万元"
    document = make_document(session, text=text)
    fields = {
        "source_entry_index": 1,
        "punished_entity": "某保险公司",
        "illegal_facts": text,
        "penalty_result": "罚款10万元",
        "original_sales_wording_disclosed": False,
        "source_quote": text,
    }
    evidence = {}
    for field_name, quote in (
        ("punished_entity", "某保险公司"),
        ("illegal_facts", text),
        ("penalty_result", "罚款10万元"),
    ):
        start = text.index(quote)
        evidence[field_name] = [
            {
                "quote": quote,
                "page_number": 1,
                "start_offset": start,
                "end_offset": start + len(quote),
                "mode": "verbatim",
            }
        ]
    fragments = build_penalty_source_entry_fragments(fields, evidence)
    content_sha256 = source_entry_content_sha256(build_penalty_identity_material(fields, evidence))
    candidate = StructuredDraftEnvelope.model_validate(
        {
            "document_id": document.id,
            "record_type": DataType.PENALTY.value,
            "source_entry_locator": {"table_index": 1, "logical_row": 1},
            "source_entry_fragments": fragments,
            "source_entry_content_sha256": content_sha256,
            "fields": {
                **fields,
                "source_entry_fingerprint": penalty_source_entry_fingerprint(
                    raw_artifact_sha256=document.sha256,
                    source_entry_content_sha256=content_sha256,
                ),
            },
            "field_evidence": evidence,
        }
    )

    record = service(session).import_draft(session, candidate)

    assert record.punished_entity == "某保险公司"
    assert len(fragments) == 3


@pytest.mark.parametrize("missing_field", ["punished_entity", "penalty_result"])
def test_identity_requires_entity_and_result_fragments(session, missing_field: str) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    payload = complete_envelope(document).model_dump(mode="json")
    evidence_item = payload["field_evidence"][missing_field][0]
    payload["source_entry_fragments"] = [
        item
        for item in payload["source_entry_fragments"]
        if item["start_offset"] != evidence_item["start_offset"]
    ]
    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_fragment_set_mismatch",
    ):
        service(session).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_extra_unrelated_fragment_is_rejected(session) -> None:
    document = make_document(
        session,
        text="某保险公司\n罚款10万元\n销售误导\n无关标题文字",
    )
    payload = complete_envelope(document).model_dump(mode="json")
    unrelated = "无关标题文字"
    start = (document.raw_text or "").index(unrelated)
    payload["source_entry_fragments"].append(
        {
            "quote": unrelated,
            "start_offset": start,
            "end_offset": start + len(unrelated),
        }
    )

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_fragment_set_mismatch",
    ):
        service(session).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_missing_illegal_facts_fragment_is_rejected(session) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    payload = complete_envelope(document).model_dump(mode="json")
    fact = payload["field_evidence"]["illegal_facts"][0]
    payload["source_entry_fragments"] = [
        item
        for item in payload["source_entry_fragments"]
        if item["start_offset"] != fact["start_offset"]
    ]

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_fragment_set_mismatch",
    ):
        service(session).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_penalty_punished_entity_is_required(session) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    payload = complete_envelope(document).model_dump(mode="json")
    payload["fields"]["punished_entity"] = None
    payload["field_evidence"].pop("punished_entity")
    entity_quote = "某保险公司"
    payload["source_entry_fragments"] = [
        item for item in payload["source_entry_fragments"] if item["quote"] != entity_quote
    ]
    payload["source_entry_content_sha256"] = "a" * 64
    payload["fields"]["source_entry_fingerprint"] = "b" * 64

    with pytest.raises(
        StructuredRecordError,
        match="penalty_punished_entity_required",
    ):
        service(session).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_same_identity_quotes_at_different_offsets_have_same_fingerprint(session) -> None:
    block = "某保险公司\n罚款10万元\n销售误导"
    document = make_document(session, text=f"{block}\n分隔文字\n{block}")
    first = complete_envelope(document, index=1)
    second_payload = complete_envelope(
        document,
        index=2,
        locator={"table_index": 2, "numbered_entry": 2},
    ).model_dump(mode="json")
    second_block_start = (document.raw_text or "").rindex(block)
    first_block_start = (document.raw_text or "").index(block)
    shift = second_block_start - first_block_start
    for items in second_payload["field_evidence"].values():
        for item in items:
            item["start_offset"] += shift
            item["end_offset"] += shift
    for item in second_payload["source_entry_fragments"]:
        item["start_offset"] += shift
        item["end_offset"] += shift
    second = StructuredDraftEnvelope.model_validate(second_payload)

    assert first.fields["source_entry_fingerprint"] == second.fields["source_entry_fingerprint"]
    service(session).import_draft(session, first)
    with pytest.raises(StructuredRecordError, match="penalty_source_entry_duplicate"):
        service(session).import_draft(session, second)


def test_source_entry_content_hash_mismatch_is_rejected(session) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    payload = complete_envelope(document).model_dump(mode="json")
    payload["source_entry_content_sha256"] = "f" * 64

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_content_sha256_mismatch",
    ):
        service(session).import_draft(
            session,
            StructuredDraftEnvelope.model_validate(payload),
        )


def test_nfra_locator_validation_fails_closed(session) -> None:
    invalid_locators = [
        {"nfra_doc_id": "999", "table_index": 1, "logical_row": 1},
        {"nfra_doc_id": "123", "table_index": True, "logical_row": 1},
        {
            "nfra_doc_id": "123",
            "table_index": 1,
            "logical_row": 1,
            "numbered_entry": 1,
        },
        {"nfra_doc_id": "123", "table_index": 1, "random": 1},
    ]
    for position, locator in enumerate(invalid_locators, 1):
        document = make_document(
            session,
            text=f"某保险公司\n罚款10万元\n销售误导\n{position}",
            source_url=f"https://example.test/invalid-locator-{position}",
            nfra_doc_id="123",
        )
        candidate = complete_envelope(document, locator=locator)

        with pytest.raises(
            StructuredRecordError,
            match="penalty_source_entry_locator_invalid",
        ):
            service(session).import_draft(session, candidate)


def test_nfra_parsed_metadata_requires_nonempty_doc_id(session) -> None:
    document = make_document(
        session,
        text="某保险公司\n罚款10万元\n销售误导",
        parsed_metadata={"source_format": "nfra_public_json", "nfra": {}},
    )
    candidate = complete_envelope(
        document,
        locator={"nfra_doc_id": "caller-value", "table_index": 1, "logical_row": 1},
    )

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_locator_invalid",
    ):
        service(session).import_draft(session, candidate)


def test_non_nfra_artifact_rejects_nfra_locator(session) -> None:
    document = make_document(
        session,
        text="某保险公司\n罚款10万元\n销售误导",
        parsed_metadata={"source_format": "pdf"},
    )
    candidate = complete_envelope(
        document,
        locator={"nfra_doc_id": "123", "table_index": 1, "logical_row": 1},
    )

    with pytest.raises(
        StructuredRecordError,
        match="penalty_source_entry_locator_invalid",
    ):
        service(session).import_draft(session, candidate)


@pytest.mark.parametrize(
    "field_name",
    [
        "source_entry_index",
        "source_entry_fingerprint",
        "source_entry_content_sha256",
        "source_entry_fragments",
        "source_entry_locator",
    ],
)
def test_penalty_source_identity_is_locked_during_revision(
    session,
    field_name: str,
) -> None:
    document = make_document(session)
    record = service(session).import_draft(session, envelope(document, 1, "违法事实一"))
    revision = StructuredDraftRevisionEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        structured_record_id=record.id,
        fields={field_name: "changed"},
        field_evidence={},
    )

    with pytest.raises(StructuredRecordError, match="penalty_source_identity_locked"):
        StructuredDraftRevisionService(Settings(data_dir=session.info["data_dir"])).revise(
            session, revision, reason="不得修改身份", actor="tester"
        )


def test_duplicate_candidate_does_not_mutate_approved_existing_record(session) -> None:
    first_document = make_document(session, source_url="https://example.test/protected-a")
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, "违法事实一"),
    )
    first_document.final_review_status = ReviewStatus.APPROVED.value
    session.commit()
    review_service = ReviewService(Settings(data_dir=session.info["data_dir"]))
    before = review_service._document_review_row(session, first_document, 1, 1)
    before_key = before["parsed_fields"]["records"][0]["portable_record_key"]
    before_hash = payload_hash(before)
    second_document = make_document(
        session,
        text="违法事实一\n违法事实二\n修订事实一\n另一原件",
        source_url="https://example.test/protected-b",
    )

    second = service(session).import_draft(
        session,
        envelope(second_document, 1, "违法事实一"),
    )

    assert second.duplicate_candidate is True
    assert first.duplicate_candidate is False
    after = review_service._document_review_row(session, first_document, 1, 1)
    assert after["parsed_fields"]["records"][0]["portable_record_key"] == before_key
    assert payload_hash(after) == before_hash


def test_duplicate_candidate_does_not_mutate_indexed_existing_record(session) -> None:
    first_document = make_document(session, source_url="https://example.test/indexed-a")
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, "违法事实一"),
    )
    first_document.knowledge_index_status = KnowledgeIndexStatus.INDEXED.value
    first_document.indexed_at = datetime.now(UTC)
    session.commit()
    second_document = make_document(
        session,
        text="违法事实一\n违法事实二\n修订事实一\n索引保护",
        source_url="https://example.test/indexed-b",
    )

    second = service(session).import_draft(
        session,
        envelope(second_document, 1, "违法事实一"),
    )

    assert second.duplicate_candidate is True
    assert first.duplicate_candidate is False


def test_verified_rejected_existing_record_is_fully_protected(session) -> None:
    first_document = make_document(session, source_url="https://example.test/rejected-a")
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, "违法事实一"),
    )
    first_document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
    first_document.final_review_status = ReviewStatus.REJECTED.value
    session.commit()
    review_service = ReviewService(Settings(data_dir=session.info["data_dir"]))
    before_row = review_service._document_review_row(session, first_document, 1, 1)
    before_record = {
        "duplicate_candidate": first.duplicate_candidate,
        "fields": (
            first.punished_entity,
            first.document_number,
            first.illegal_facts,
            first.penalty_result,
            first.source_entry_fingerprint,
        ),
        "evidence": json.loads(json.dumps(first.field_evidence_json)),
        "metadata": json.loads(json.dumps(first_document.metadata_json)),
        "portable_record_key": before_row["parsed_fields"]["records"][0]["portable_record_key"],
        "payload_hash": payload_hash(before_row),
    }
    second_document = make_document(
        session,
        text="违法事实一\n另一原件",
        source_url="https://example.test/rejected-b",
    )

    second = service(session).import_draft(
        session,
        envelope(second_document, 1, "违法事实一"),
    )

    assert second.duplicate_candidate is True
    assert first.duplicate_candidate is False
    assert (
        first.punished_entity,
        first.document_number,
        first.illegal_facts,
        first.penalty_result,
        first.source_entry_fingerprint,
    ) == before_record["fields"]
    assert first.field_evidence_json == before_record["evidence"]
    assert first_document.metadata_json == before_record["metadata"]
    after_row = review_service._document_review_row(session, first_document, 1, 1)
    assert (
        after_row["parsed_fields"]["records"][0]["portable_record_key"]
        == before_record["portable_record_key"]
    )
    assert payload_hash(after_row) == before_record["payload_hash"]


def test_review_decision_protects_existing_record_from_duplicate_mutation(session) -> None:
    first_document = make_document(session, source_url="https://example.test/decision-a")
    first = service(session).import_draft(
        session,
        envelope(first_document, 1, "违法事实一"),
    )
    batch = ReviewBatch(
        batch_name="protected",
        data_type=DataType.PENALTY.value,
        record_count=1,
        export_path="/tmp/protected.jsonl",
        export_sha256="a" * 64,
        schema_version="2.0",
        status="completed",
    )
    session.add(batch)
    session.flush()
    session.add(
        ReviewDecision(
            batch_id=batch.id,
            record_type=DataType.PENALTY.value,
            record_id=first_document.id,
            decision=ReviewStatus.REJECTED.value,
            field_reviews_json={},
            corrections_json={},
            evidence_quality="A",
            reviewer="reviewer",
            reviewed_payload_hash="b" * 64,
            schema_version="2.0",
        )
    )
    session.commit()
    second_document = make_document(
        session,
        text="违法事实一\n另一决定原件",
        source_url="https://example.test/decision-b",
    )

    second = service(session).import_draft(
        session,
        envelope(second_document, 1, "违法事实一"),
    )

    assert second.duplicate_candidate is True
    assert first.duplicate_candidate is False


def test_fragment_set_failure_rolls_back_whole_jsonl_batch(session, tmp_path: Path) -> None:
    document = make_document(
        session,
        text="违法事实一\n违法事实二\n修订事实一\n无关片段",
    )
    first = envelope(document, 1, "违法事实一")
    invalid = envelope(document, 2, "违法事实二").model_dump(mode="json")
    unrelated = "无关片段"
    start = (document.raw_text or "").index(unrelated)
    invalid["source_entry_fragments"].append(
        {
            "quote": unrelated,
            "start_offset": start,
            "end_offset": start + len(unrelated),
        }
    )
    path = tmp_path / "fragment-set-mismatch.jsonl"
    path.write_text(
        "\n".join(
            (
                json.dumps(first.model_dump(mode="json"), ensure_ascii=False),
                json.dumps(invalid, ensure_ascii=False),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = service(session).import_jsonl(session, path)

    assert imported == 0
    assert "penalty_source_entry_fragment_set_mismatch" in errors[0]
    assert session.query(Penalty).filter_by(document_id=document.id).count() == 0


def test_penalty_identity_provenance_is_saved_and_bound_to_portable_key(session) -> None:
    document = make_document(session, text="某保险公司\n罚款10万元\n销售误导")
    record = service(session).import_draft(session, complete_envelope(document))
    provenance = document.metadata_json["structured_draft_provenance"][0]

    assert provenance["structured_record_id"] == record.id
    assert len(provenance["source_entry_fragments"]) == 3
    assert provenance["source_entry_content_sha256"] == source_entry_content_sha256(
        build_penalty_identity_material(
            {
                "punished_entity": record.punished_entity,
                "penalty_result": record.penalty_result,
                "illegal_facts": record.illegal_facts,
                "document_number": record.document_number,
            },
            record.field_evidence_json,
        )
    )
    row = ReviewService(Settings(data_dir=session.info["data_dir"]))._document_review_row(
        session,
        document,
        1,
        1,
    )
    exported = row["parsed_fields"]["records"][0]
    assert (
        exported["draft_provenance"]["source_entry_fragments"]
        == provenance["source_entry_fragments"]
    )
