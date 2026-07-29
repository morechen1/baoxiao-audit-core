from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.models import Penalty, SourceDocument
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
    exact_source_entry_text_sha256,
    penalty_source_entry_fingerprint,
)
from app.services.review import ReviewService
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
    locator: dict[str, str | int] | None = None,
) -> StructuredDraftEnvelope:
    stable_locator = locator or {"table_index": 1, "row_index": index}
    text_sha256 = exact_source_entry_text_sha256(text)
    fingerprint = penalty_source_entry_fingerprint(
        raw_artifact_sha256=document.sha256,
        source_entry_index=index,
        stable_source_locator=stable_locator,
        exact_source_entry_text_sha256=text_sha256,
    )
    start = (document.raw_text or "").index(text)
    return StructuredDraftEnvelope(
        document_id=document.id,
        record_type=DataType.PENALTY,
        source_entry_locator=stable_locator,
        source_entry_text=text,
        source_entry_text_sha256=text_sha256,
        fields={
            "source_entry_index": index,
            "source_entry_fingerprint": fingerprint,
            "illegal_facts": text,
            "original_sales_wording_disclosed": False,
            "source_quote": text,
        },
        field_evidence={
            "illegal_facts": [
                {
                    "quote": text,
                    "page_number": 1,
                    "start_offset": start,
                    "end_offset": start + len(text),
                    "mode": "verbatim",
                }
            ]
        },
    )


def service(session) -> StructuredRecordService:
    return StructuredRecordService(Settings(data_dir=session.info["data_dir"]))


def test_penalty_fingerprint_is_stable_and_has_fixed_serialization() -> None:
    inputs = {
        "raw_artifact_sha256": "a" * 64,
        "source_entry_index": 2,
        "stable_source_locator": {"row": 2, "table": "处罚"},
        "exact_source_entry_text_sha256": "b" * 64,
    }

    first = penalty_source_entry_fingerprint(**inputs)
    second = penalty_source_entry_fingerprint(
        **{**inputs, "stable_source_locator": {"table": "处罚", "row": 2}}
    )

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("raw_artifact_sha256", "c" * 64),
        ("source_entry_index", 3),
        ("stable_source_locator", {"row": 3, "table": "处罚"}),
        ("exact_source_entry_text_sha256", "d" * 64),
    ],
)
def test_penalty_fingerprint_binds_every_material_input(
    field: str,
    replacement: object,
) -> None:
    inputs = {
        "raw_artifact_sha256": "a" * 64,
        "source_entry_index": 2,
        "stable_source_locator": {"row": 2, "table": "处罚"},
        "exact_source_entry_text_sha256": "b" * 64,
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
        locator={"table_index": 1, "row_index": 2},
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
