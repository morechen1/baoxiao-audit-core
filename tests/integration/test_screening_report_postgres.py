from __future__ import annotations

import os
from collections.abc import Generator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.services.screening.evidence import evaluate_semantic_support
from app.services.screening.reports import (
    ScreeningReportService,
    canonical_report_sha256,
)
from app.services.screening.rules import load_ruleset


@pytest.fixture
def postgres_report_urls() -> Generator[tuple[str, str], None, None]:
    configured = os.environ.get("DATABASE_URL", "")
    configured_url = make_url(configured) if configured else None
    if (
        configured_url is None
        or configured_url.get_backend_name() != "postgresql"
        or "test" not in (configured_url.database or "")
    ):
        pytest.skip("dedicated PostgreSQL test database is required")
    admin_url = configured_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    names = [f"baoxiao_test_report_{uuid4().hex[:12]}" for _ in range(2)]
    quoted = [admin_engine.dialect.identifier_preparer.quote(name) for name in names]
    with admin_engine.connect() as connection:
        for name in quoted:
            connection.exec_driver_sql(f"CREATE DATABASE {name}")
    urls: tuple[str, str] = tuple(
        configured_url.set(database=name).render_as_string(False) for name in names
    )  # type: ignore[assignment]
    try:
        yield urls
    finally:
        with admin_engine.connect() as connection:
            for name in quoted:
                connection.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
        admin_engine.dispose()


def _database_ids(database_url: str, start: int) -> tuple[int, int, int, int]:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SEQUENCE report_identity_test START WITH {start}"))
        values = tuple(
            int(connection.scalar(text("SELECT nextval('report_identity_test')"))) for _ in range(4)
        )
    engine.dispose()
    return values  # type: ignore[return-value]


def _reports(ids: tuple[int, int, int, int], reverse_links: bool) -> tuple[dict, dict]:
    material_id, run_id, first_link_id, second_link_id = ids
    rule = next(
        value for value in load_ruleset().rules if value.rule_id == "regulatory_endorsement"
    )
    matcher = rule.evidence_matchers["normative_basis"]
    quote = "不得利用监管机构审核或备案程序提供保证等引人误解的表述"
    semantic = evaluate_semantic_support(matcher, quote)

    def link(link_id: int, rank: int, pilot_id: str, identity: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=link_id,
            support_type="normative_basis",
            retrieval_rank=rank,
            retrieval_score=10.0 - rank,
            chunk_identity_sha256=identity,
            chunk_content_sha256=identity,
            source_document_snapshot_json={
                "source_document_id": link_id + 1000,
                "record_type": "regulation",
                "chunk_kind": "article_text",
                "pilot_id": pilot_id,
                "title": "保险销售行为管理办法",
                "source_url": "https://example.test/regulation",
                "authenticity_status": "verified_public",
                "review_status": "approved",
            },
            source_locator_snapshot_json={"article_number": "第十七条"},
            evidence_references_snapshot_json=[{"field_name": "article_text", "quote": quote}],
            support_evaluation_version="deterministic_evidence_support_v2",
            support_evaluation_passed=True,
            matched_support_patterns=list(semantic["matched_patterns"]),
            actual_matched_substrings=list(semantic["actual_matched_substrings"]),
            matched_pattern_groups=list(semantic["matched_pattern_groups"]),
            matched_evidence_fields=["article_text"],
            support_reason="semantic_evidence_match",
            semantic_support_score=semantic["score"],
            semantic_support_reason=semantic["reason"],
            context_scope="not_applicable",
        )

    links = [
        link(first_link_id, 1, "REG-001", "a" * 64),
        link(second_link_id, 2, "REG-003", "b" * 64),
    ]
    if reverse_links:
        links.reverse()
    finding = SimpleNamespace(
        finding_sha256="c" * 64,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        rule_snapshot_sha256="d" * 64,
        category=rule.category,
        severity=rule.severity,
        signal_strength=rule.signal_strength,
        matched_text="监管推荐",
        raw_start_offset=0,
        raw_end_offset=4,
        explanation=rule.explanation_template,
        review_question=rule.review_question_template,
        remediation_template=rule.institution_remediation_template,
        consumer_notice_template=rule.consumer_notice_template,
        rule_snapshot_json=rule.model_dump(mode="json"),
        evidence_status="partially_supported",
        evidence_links=links,
    )
    run = SimpleNamespace(
        id=run_id,
        ruleset_version=load_ruleset().ruleset_version,
        trusted_index_payload_hash="e" * 64,
    )
    material = SimpleNamespace(
        id=material_id,
        title="跨数据库报告稳定性",
        input_sha256="f" * 64,
        raw_text="监管推荐",
    )
    service = ScreeningReportService()
    service._load = lambda _session, _run_id: (run, material, [finding])  # type: ignore[method-assign]
    return service.institution_report(None, run_id), service.consumer_notice(None, run_id)  # type: ignore[arg-type]


def test_postgres_reports_are_stable_across_databases_and_primary_keys(
    postgres_report_urls: tuple[str, str],
) -> None:
    first_ids = _database_ids(postgres_report_urls[0], 1)
    second_ids = _database_ids(postgres_report_urls[1], 10_001)
    first_institution, first_consumer = _reports(first_ids, False)
    second_institution, second_consumer = _reports(second_ids, True)
    assert canonical_report_sha256(first_institution) == canonical_report_sha256(second_institution)
    assert canonical_report_sha256(first_consumer) == canonical_report_sha256(second_consumer)
    assert first_institution["evidence_summary"]["selected_irrelevant_links"] == 0
