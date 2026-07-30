from __future__ import annotations

import hashlib
import os
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ReviewDecisionError
from app.models import (
    AuthenticityDecisionLog,
    Penalty,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    KnowledgeIndexStatus,
    ReviewStatus,
)
from app.services.knowledge import KnowledgeIndexService
from app.services.parsed_artifacts import canonical_json_bytes, text_sha256
from app.services.review import ReviewService
from app.services.validation import ValidationService
from migrations.versions import b9c8d7e6f5a4_support_multi_record_penalties as migration

V0_8_REVISION = "a8b7c6d5e4f3"


@pytest.fixture
def postgres_migration_url(monkeypatch) -> Generator[str, None, None]:
    configured = os.environ.get("DATABASE_URL", "")
    database_name = make_url(configured).database if configured else None
    if not configured.startswith("postgresql") or "test" not in (database_name or ""):
        pytest.skip("dedicated PostgreSQL test database is required")
    configured_url = make_url(configured)
    database_name = f"baoxiao_test_legacy_{uuid4().hex[:12]}"
    admin_url = configured_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    quoted_name = admin_engine.dialect.identifier_preparer.quote(database_name)
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
    test_url: URL = configured_url.set(database=database_name)
    monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        yield test_url.render_as_string(hide_password=False)
    finally:
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted_name} WITH (FORCE)")
        admin_engine.dispose()


def _evidence_item(raw_text: str, quote: str) -> dict[str, object]:
    start = raw_text.index(quote)
    return {
        "quote": quote,
        "page_number": 1,
        "start_offset": start,
        "end_offset": start + len(quote),
        "mode": "verbatim",
    }


def _insert_v0_8_penalty(
    database_url: str,
    data_dir: Path,
    provenance: list[dict[str, object]],
) -> dict[str, object]:
    raw_text = "监管机关\n某保险公司\n乌金罚决字〔2025〕9号\n销售误导\n罚款10万元"
    raw_dir = data_dir / "raw"
    parsed_dir = data_dir / "parsed_artifacts"
    raw_dir.mkdir(parents=True)
    parsed_dir.mkdir(parents=True)
    raw_path = raw_dir / "legacy-penalty.txt"
    raw_path.write_text(raw_text, encoding="utf-8")
    raw_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    parser_name = "LegacyPenaltyTestParser"
    parser_version = "1.0"
    artifact = {
        "schema_version": "2.0",
        "raw_sha256": raw_sha256,
        "parser_name": parser_name,
        "parser_version": parser_version,
        "title": "历史处罚决定",
        "plain_text": raw_text,
        "pages": [
            {
                "page_number": 1,
                "text": raw_text,
                "start_offset": 0,
                "end_offset": len(raw_text),
            }
        ],
        "warnings": [],
        "metadata": {},
    }
    artifact_bytes = canonical_json_bytes(artifact)
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    artifact_path = parsed_dir / f"{raw_sha256}-{artifact_sha256}.json"
    artifact_path.write_bytes(artifact_bytes)
    evidence = {
        "authority": [_evidence_item(raw_text, "监管机关")],
        "punished_entity": [_evidence_item(raw_text, "某保险公司")],
        "document_number": [_evidence_item(raw_text, "乌金罚决字〔2025〕9号")],
        "illegal_facts": [_evidence_item(raw_text, "销售误导")],
        "penalty_result": [_evidence_item(raw_text, "罚款10万元")],
    }
    metadata = {
        "existing_metadata": {"preserved": True},
        "structured_draft_provenance": provenance,
    }
    engine = create_engine(database_url)
    json_parameters = (
        sa.bindparam("metadata_json", type_=sa.JSON()),
        sa.bindparam("corrected_fields_json", type_=sa.JSON()),
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_documents (
                    id, source_id, data_type, source_url, final_url, source_title,
                    publisher, published_at, collected_at, content_type, raw_file_path,
                    raw_text, sha256, parsed_artifact_path, parsed_artifact_sha256,
                    parsed_text_sha256, parsed_from_raw_sha256, parser_name,
                    parser_version, parsed_at, http_status, authenticity_type,
                    collection_status, parse_status, final_review_status, metadata_json,
                    corrected_fields_json, knowledge_index_status, indexed_at,
                    created_at, updated_at
                ) VALUES (
                    1, NULL, 'penalty', 'https://official.example/legacy-penalty',
                    'https://official.example/legacy-penalty', '历史处罚决定',
                    '监管机关', NULL, CURRENT_TIMESTAMP, 'text/plain', :raw_file_path,
                    :raw_text, :raw_sha256, :parsed_artifact_path,
                    :parsed_artifact_sha256, :parsed_text_sha256, :raw_sha256,
                    :parser_name, :parser_version, CURRENT_TIMESTAMP, 200,
                    'pending_verification', 'collected', 'parsed', 'parsed',
                    :metadata_json, :corrected_fields_json, 'not_indexed', NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ).bindparams(*json_parameters),
            {
                "raw_file_path": str(raw_path),
                "raw_text": raw_text,
                "raw_sha256": raw_sha256,
                "parsed_artifact_path": str(artifact_path),
                "parsed_artifact_sha256": artifact_sha256,
                "parsed_text_sha256": text_sha256(raw_text),
                "parser_name": parser_name,
                "parser_version": parser_version,
                "metadata_json": metadata,
                "corrected_fields_json": {},
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO penalties (
                    id, document_id, punished_entity, authority, document_number,
                    decision_date, illegal_facts, legal_basis, penalty_result,
                    original_sales_wording_disclosed, original_sales_wording,
                    source_quote, field_evidence_json, final_review_status
                ) VALUES (
                    1, 1, '某保险公司', '监管机关', '乌金罚决字〔2025〕9号',
                    NULL, '销售误导', NULL, '罚款10万元', false, NULL,
                    '销售误导', :field_evidence_json, 'parsed'
                )
                """
            ).bindparams(
                sa.bindparam("field_evidence_json", type_=sa.JSON()),
            ),
            {"field_evidence_json": evidence},
        )
    engine.dispose()
    return {
        "raw_text": raw_text,
        "raw_sha256": raw_sha256,
        "field_evidence": evidence,
    }


def _legacy_provenance() -> list[dict[str, object]]:
    return [
        {
            "structured_record_id": 1,
            "record_type": "penalty",
            "pilot_id": "PEN-LEGACY-001",
            "draft_generation_method": "legacy_manual_import",
            "draft_generation_version": "v0.8",
        }
    ]


def test_postgres_v0_8_penalty_upgrade_is_explicitly_isolated(
    postgres_migration_url: str,
    tmp_path: Path,
) -> None:
    command.upgrade(Config("alembic.ini"), V0_8_REVISION)
    original = _insert_v0_8_penalty(
        postgres_migration_url,
        tmp_path / "legacy-data",
        _legacy_provenance(),
    )

    command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(postgres_migration_url)
    settings = Settings(
        database_url=postgres_migration_url,
        data_dir=tmp_path / "legacy-data",
    )
    with Session(engine, expire_on_commit=False) as session:
        document = session.get(SourceDocument, 1)
        penalty = session.get(Penalty, 1)
        assert document is not None
        assert penalty is not None
        assert session.scalar(select(sa.func.count()).select_from(Penalty)) == 1
        assert penalty.punished_entity == "某保险公司"
        assert penalty.document_number == "乌金罚决字〔2025〕9号"
        assert penalty.illegal_facts == "销售误导"
        assert penalty.penalty_result == "罚款10万元"
        assert penalty.field_evidence_json == original["field_evidence"]
        assert penalty.source_entry_index == 1
        assert penalty.source_entry_fingerprint == migration._legacy_fingerprint(
            str(original["raw_sha256"]),
            "销售误导",
            str(original["raw_text"]),
        )
        provenance = document.metadata_json["structured_draft_provenance"][0]
        assert provenance["pilot_id"] == "PEN-LEGACY-001"
        assert provenance["draft_generation_method"] == "legacy_manual_import"
        assert provenance["draft_generation_version"] == "v0.8"
        assert provenance["identity_version"] == "legacy_source_quote_v1"
        assert provenance["source_identity_status"] == "reimport_required"
        assert "source_entry_locator" not in provenance
        assert "source_entry_fragments" not in provenance
        assert "source_entry_content_sha256" not in provenance

        validation = ValidationService(settings).evaluate_document(
            session,
            document,
            [penalty],
        )
        assert validation.valid is False
        assert any(
            issue.validator == "PenaltySourceIdentityValidator"
            and issue.code == "penalty_source_identity_reimport_required"
            for issue in validation.issues
        )

        document.final_review_status = ReviewStatus.REQUIRES_EXPERT_REVIEW.value
        penalty.final_review_status = ReviewStatus.REQUIRES_EXPERT_REVIEW.value
        session.commit()
        with pytest.raises(
            ReviewDecisionError,
            match="penalty_source_identity_reimport_required",
        ):
            ReviewService(settings).export_batch(session, "penalty", "jsonl")
        session.rollback()

        document = session.get(SourceDocument, 1)
        penalty = session.get(Penalty, 1)
        assert document is not None
        assert penalty is not None
        document.final_review_status = ReviewStatus.APPROVED.value
        document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
        document.metadata_json = {
            **document.metadata_json,
            "automatic_validation": {"valid": True, "issues": []},
        }
        penalty.final_review_status = ReviewStatus.APPROVED.value
        session.commit()
        reasons = KnowledgeIndexService(settings).rejection_reasons(session, document)
        assert "penalty_source_identity_reimport_required" in reasons
        assert document.knowledge_index_status == KnowledgeIndexStatus.NOT_INDEXED.value
        assert session.scalar(select(sa.func.count()).select_from(ReviewDecision)) == 0
        assert session.scalar(select(sa.func.count()).select_from(AuthenticityDecisionLog)) == 0
    engine.dispose()


@pytest.mark.parametrize(
    "provenance",
    [
        [],
        [
            {
                "structured_record_id": 1,
                "record_type": "penalty",
                "pilot_id": "PEN-LEGACY-001",
                "draft_generation_method": "legacy_manual_import",
                "draft_generation_version": "v0.8",
            },
            {
                "structured_record_id": 1,
                "record_type": "penalty",
                "pilot_id": "PEN-LEGACY-001",
                "draft_generation_method": "legacy_manual_import",
                "draft_generation_version": "v0.8",
            },
        ],
    ],
)
def test_postgres_v0_8_penalty_upgrade_rejects_unbound_provenance(
    postgres_migration_url: str,
    tmp_path: Path,
    provenance: list[dict[str, object]],
) -> None:
    command.upgrade(Config("alembic.ini"), V0_8_REVISION)
    _insert_v0_8_penalty(
        postgres_migration_url,
        tmp_path / "legacy-invalid-data",
        provenance,
    )

    with pytest.raises(
        RuntimeError,
        match="cannot_backfill_legacy_penalty_identity_marker",
    ):
        command.upgrade(Config("alembic.ini"), "head")

    engine = create_engine(postgres_migration_url)
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        columns = {item["name"] for item in inspect(connection).get_columns("penalties")}
        assert current == V0_8_REVISION
        assert "source_entry_index" not in columns
        assert "source_entry_fingerprint" not in columns
        assert connection.execute(text("SELECT count(*) FROM penalties")).scalar_one() == 1
    engine.dispose()
