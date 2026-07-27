import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import CollectionError, ReviewDecisionError
from app.models import DataSource, DocumentOccurrence, Regulation
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.collection import (
    CollectionResult,
    FileCollector,
    LocalManifestCollector,
)
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.review import ReviewService
from app.services.review.service import payload_hash


def test_sha256_deduplication(session, tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("same content", encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    collector = FileCollector(settings)

    first, first_created = collector.persist(
        session,
        collector.collect(source),
        DataType.REGULATION.value,
        authenticity_type=AuthenticityType.DEMO_ONLY.value,
    )
    second, second_created = collector.persist(
        session,
        collector.collect(source),
        DataType.REGULATION.value,
        authenticity_type=AuthenticityType.DEMO_ONLY.value,
    )

    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert first.sha256 == second.sha256


def test_same_content_from_different_urls_preserves_occurrences(session, tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    collector = FileCollector(settings)
    first_result = CollectionResult(
        content=b"same public content",
        source_url="https://one.example.test/rule",
        final_url="https://one.example.test/rule",
        content_type="text/plain",
        http_status=200,
    )
    second_result = CollectionResult(
        content=b"same public content",
        source_url="https://two.example.test/rule",
        final_url="https://two.example.test/rule",
        content_type="text/plain",
        http_status=200,
    )

    first, created = collector.persist(session, first_result, DataType.REGULATION.value)
    second, second_created = collector.persist(session, second_result, DataType.REGULATION.value)

    assert created is True
    assert second_created is False
    assert first.id == second.id
    assert len(first.occurrences) == 2
    assert {item.source_url for item in first.occurrences} == {
        first_result.source_url,
        second_result.source_url,
    }


def test_collector_cannot_assign_verified_public_directly(session, tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("public content", encoding="utf-8")
    collector = FileCollector(Settings(database_url="sqlite://", data_dir=tmp_path / "data"))

    with pytest.raises(ValueError, match="audited human review"):
        collector.persist(
            session,
            collector.collect(source),
            DataType.REGULATION.value,
            authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        )


def registered_source(session, *, publisher: str = "正式发布机构") -> DataSource:
    source = DataSource(
        name="官方来源",
        base_url="https://official.example",
        publisher=publisher,
        source_type=DataType.REGULATION.value,
        enabled=True,
        crawl_policy={"allowed_domains": ["cdn.official.example"]},
    )
    session.add(source)
    session.commit()
    return source


def make_reviewable_regulation(session, document) -> None:
    document.raw_text = "第一条 正式规则内容"
    document.parse_status = "parsed"
    document.final_review_status = ReviewStatus.PENDING_REVIEW.value
    document.metadata_json = {
        **document.metadata_json,
        "automatic_validation": {"valid": True, "issues": []},
    }
    ParsedArtifactService(Settings(data_dir=session.info["data_dir"])).persist(
        session,
        document,
        ParsedDocument(
            title="正式规则",
            plain_text=document.raw_text,
            pages=[ParsedPage(page_number=1, text=document.raw_text)],
        ),
        parser_name="TestParser",
    )
    quote = "正式规则内容"
    start = document.raw_text.find(quote)
    title = "正式规则"
    title_start = document.raw_text.find(title)
    session.add(
        Regulation(
            document_id=document.id,
            title="正式规则",
            article_text=quote,
            source_quote=quote,
            field_evidence_json={
                "title": [
                    {
                        "quote": title,
                        "page_number": 1,
                        "start_offset": title_start,
                        "end_offset": title_start + len(title),
                        "mode": "verbatim",
                    }
                ],
                "article_text": [
                    {
                        "quote": quote,
                        "page_number": 1,
                        "start_offset": start,
                        "end_offset": start + len(quote),
                        "mode": "verbatim",
                    }
                ],
            },
            final_review_status=ReviewStatus.PENDING_REVIEW.value,
        )
    )
    session.commit()


def review_payload(row: dict, occurrence_id: int) -> dict:
    return {
        "batch_id": row["batch_id"],
        "batch_item_id": row["batch_item_id"],
        "reviewed_payload_hash": payload_hash(row),
        "schema_version": "2.0",
        "record_id": row["record_id"],
        "record_type": row["record_type"],
        "final_status": ReviewStatus.APPROVED.value,
        "field_reviews": {},
        "corrections": {},
        "authenticity_decision": {
            "new_type": AuthenticityType.VERIFIED_PUBLIC.value,
            "verified_occurrence_id": occurrence_id,
            "reason": "已核对官方网站、文件原文及发布机构",
        },
        "evidence_quality": "A",
        "review_comment": "已核对",
        "reviewer": "reviewer",
    }


def test_local_manifest_can_complete_occurrence_authenticity_review(
    session, tmp_path: Path
) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    import_dir = settings.data_dir / "import"
    import_dir.mkdir(parents=True)
    local_file = import_dir / "rule.txt"
    local_file.write_text("第一条 正式规则内容", encoding="utf-8")
    source = registered_source(session)
    manifest = import_dir / "local_import_manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "path": str(local_file),
                "source_id": source.id,
                "source_type": DataType.REGULATION.value,
                "source_url": "https://official.example/rule.txt",
                "final_url": "https://cdn.official.example/rule.txt",
                "source_title": "正式规则",
                "publisher": "正式发布机构",
                "published_at": "2026-01-01",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    imported, errors = LocalManifestCollector(settings).import_jsonl(session, manifest)
    document = session.query(Regulation).first()
    assert document is None
    source_document = source.documents[0]
    make_reviewable_regulation(session, source_document)
    service = ReviewService(settings)
    batch = service.export_batch(session, DataType.REGULATION.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8"))
    occurrence_id = row["source_occurrences"][0]["occurrence_id"]
    service.apply_decision(session, review_payload(row, occurrence_id), batch.id)

    assert imported == 1
    assert errors == []
    assert source_document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value
    assert source_document.publisher == "正式发布机构"
    assert source_document.occurrences[0].publisher == "正式发布机构"
    assert source_document.metadata_json["local_manifest_import"] is True


def test_unattributed_file_occurrence_cannot_upgrade_authenticity(session, tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    local_file = tmp_path / "unattributed.txt"
    local_file.write_text("第一条 正式规则内容", encoding="utf-8")
    collector = FileCollector(settings)
    document, _ = collector.persist(
        session,
        collector.collect(local_file),
        DataType.REGULATION.value,
    )
    make_reviewable_regulation(session, document)
    service = ReviewService(settings)
    batch = service.export_batch(session, DataType.REGULATION.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8"))

    with pytest.raises(ReviewDecisionError, match="no registered source"):
        service.apply_decision(
            session,
            review_payload(row, row["source_occurrences"][0]["occurrence_id"]),
            batch.id,
        )
    assert document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
    assert document.metadata_json["official_source_declared"] is False


def test_later_official_occurrence_can_verify_initial_local_document(
    session, tmp_path: Path
) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    local_file = tmp_path / "later-attributed.txt"
    local_file.write_text("第一条 正式规则内容", encoding="utf-8")
    collector = FileCollector(settings)
    document, _ = collector.persist(
        session,
        collector.collect(local_file),
        DataType.REGULATION.value,
    )
    source = registered_source(session)
    same_content = CollectionResult(
        content=local_file.read_bytes(),
        source_url="https://official.example/later.txt",
        final_url="https://official.example/later.txt",
        content_type="text/plain",
        http_status=200,
    )
    duplicate, created = collector.persist(
        session,
        same_content,
        DataType.REGULATION.value,
        source_id=source.id,
    )
    make_reviewable_regulation(session, document)
    official_occurrence = (
        session.query(DocumentOccurrence)
        .filter_by(
            document_id=document.id,
            source_id=source.id,
        )
        .one()
    )
    service = ReviewService(settings)
    batch = service.export_batch(session, DataType.REGULATION.value, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8"))
    service.apply_decision(
        session,
        review_payload(row, official_occurrence.id),
        batch.id,
    )

    assert created is False
    assert duplicate.id == document.id
    assert len(document.occurrences) == 2
    assert document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value


def test_same_content_with_different_document_type_is_rejected(session, tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    local_file = tmp_path / "type-conflict.txt"
    local_file.write_text("same content", encoding="utf-8")
    collector = FileCollector(settings)
    collector.persist(
        session,
        collector.collect(local_file),
        DataType.REGULATION.value,
        authenticity_type=AuthenticityType.DEMO_ONLY.value,
    )

    with pytest.raises(CollectionError, match="duplicate_content_type_conflict"):
        collector.persist(
            session,
            collector.collect(local_file),
            DataType.PRODUCT_DOCUMENT.value,
            authenticity_type=AuthenticityType.DEMO_ONLY.value,
        )


def test_corrupt_stored_duplicate_is_rejected(session, tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    local_file = tmp_path / "corrupt.txt"
    local_file.write_text("same content", encoding="utf-8")
    collector = FileCollector(settings)
    document, _ = collector.persist(
        session,
        collector.collect(local_file),
        DataType.REGULATION.value,
    )
    Path(document.raw_file_path).write_text("tampered", encoding="utf-8")

    with pytest.raises(CollectionError, match="stored_artifact_corrupt"):
        collector.persist(
            session,
            collector.collect(local_file),
            DataType.REGULATION.value,
        )


def test_evaluation_sample_cannot_use_document_collector(session, tmp_path: Path) -> None:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    local_file = tmp_path / "evaluation.txt"
    local_file.write_text("evaluation", encoding="utf-8")
    collector = FileCollector(settings)

    with pytest.raises(CollectionError, match="unsupported_document_data_type"):
        collector.persist(
            session,
            collector.collect(local_file),
            DataType.EVALUATION_SAMPLE.value,
        )
