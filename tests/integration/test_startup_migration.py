from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import Settings


def test_no_llm_configuration_required() -> None:
    settings = Settings(
        database_url="sqlite://",
        llm_enabled=False,
        embedding_enabled=False,
        ocr_enabled=False,
    )

    assert settings.llm_enabled is False
    assert settings.llm_api_key == ""


def test_database_migration(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "migration.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        get_settings.cache_clear()

    tables = inspect(create_engine(url)).get_table_names()
    assert "source_documents" in tables
    assert "review_decisions" in tables
