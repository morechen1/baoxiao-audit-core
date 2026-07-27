from pathlib import Path

from app.core.config import Settings
from app.models.enums import AuthenticityType, DataType
from app.services.collection import FileCollector


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
