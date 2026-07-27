from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

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
        command.downgrade(Config("alembic.ini"), "base")
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        get_settings.cache_clear()

    tables = inspect(create_engine(url)).get_table_names()
    assert "source_documents" in tables
    assert "review_decisions" in tables
    assert "review_batch_items" in tables
    assert "document_occurrences" in tables


def test_provenance_migration_backfills_existing_authenticity_log(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "seeded-migration.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "9b8e7c6d5a4f")
        with create_engine(url).begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO data_sources "
                    "(id,name,base_url,publisher,source_type,enabled,crawl_policy,"
                    "rate_limit_seconds,created_at,updated_at) VALUES "
                    "(1,'official','https://official.example','publisher',"
                    "'regulation',1,'{}',1.0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO source_documents "
                    "(id,source_id,data_type,source_url,final_url,source_title,publisher,"
                    "collected_at,content_type,raw_file_path,sha256,authenticity_type,"
                    "collection_status,parse_status,final_review_status,metadata_json,"
                    "corrected_fields_json,knowledge_index_status,created_at,updated_at) "
                    "VALUES (1,1,'regulation','https://official.example/rule',"
                    "'https://official.example/rule','rule','publisher',CURRENT_TIMESTAMP,"
                    "'text/plain','/tmp/rule.txt',:sha,'verified_public','collected',"
                    "'parsed','approved','{}','{}','not_indexed',CURRENT_TIMESTAMP,"
                    "CURRENT_TIMESTAMP)"
                ),
                {"sha": "a" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO document_occurrences "
                    "(id,document_id,source_id,source_url,final_url,publisher,collected_at,"
                    "response_metadata) VALUES (1,1,1,'https://official.example/rule',"
                    "'https://official.example/rule','publisher',CURRENT_TIMESTAMP,'{}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO review_batches "
                    "(id,batch_name,data_type,record_count,export_path,status,created_at,"
                    "export_sha256,schema_version) VALUES "
                    "(1,'batch','regulation',1,'/tmp/batch.jsonl','completed',"
                    "CURRENT_TIMESTAMP,:sha,'2.0')"
                ),
                {"sha": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO review_decisions "
                    "(id,batch_id,record_type,record_id,decision,field_reviews_json,"
                    "corrections_json,evidence_quality,reviewer,reviewed_at,"
                    "reviewed_payload_hash,schema_version) VALUES "
                    "(1,1,'regulation',1,'approved','{}','{}','A','reviewer',"
                    "CURRENT_TIMESTAMP,:sha,'2.0')"
                ),
                {"sha": "c" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO authenticity_decision_logs "
                    "(id,document_id,previous_authenticity_type,new_authenticity_type,"
                    "reviewer,review_decision_id,reason,changed_at) VALUES "
                    "(1,1,'pending_verification','verified_public','reviewer',1,"
                    "'verified',CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(Config("alembic.ini"), "head")
        with create_engine(url).connect() as connection:
            row = connection.execute(
                text(
                    "SELECT source_id, verified_occurrence_id "
                    "FROM authenticity_decision_logs WHERE id = 1"
                )
            ).one()
    finally:
        get_settings.cache_clear()

    assert row == (1, 1)
