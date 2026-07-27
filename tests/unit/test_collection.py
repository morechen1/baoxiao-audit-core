from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.enums import AuthenticityType, DataType
from app.services.collection import CollectionResult, FileCollector


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
