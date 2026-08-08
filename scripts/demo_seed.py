"""Build the isolated, deterministic contest-demo knowledge fixture.

This file deliberately refuses any database other than ``baoxiao_demo``.  It is
not an import path for formal evidence and it never reads or restores formal
data.  The fixture still walks the normal parse, validation, indexing and
controlled-explanation paths so that the live Demo can exercise real APIs.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    Regulation,
    ReviewBatch,
    ReviewBatchItem,
    ReviewDecision,
)
from app.models.enums import AuthenticityType, DataType, ReviewStatus
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.knowledge import KnowledgeIndexService
from app.services.parsing import ParsingService
from app.services.state_machine import StateMachineService
from app.services.validation import ValidationService

DEMO_DATABASE = "baoxiao_demo"
DEMO_SOURCE_NAME = "构造赛事演示数据（非正式）"
DEMO_URL = "https://contest-demo.invalid/knowledge/marketing-risk-reference.txt"
DEMO_TITLE = "构造赛事演示：保险营销风险提示参考（非正式）"
DEMO_ARTICLE = """本参考仅用于比赛现场的构造演示，不是监管文件，也不能作为正式合规依据。
保险营销不得利用监管机构名义作推荐、担保或引人误解的宣传。
保险营销不得承诺保证收益、保本保息、无风险或者零损失。
保险营销应提示退保金额及现金价值以保险合同约定为准，并说明可能存在损失。"""


class _DemoCollector(BaseCollector):
    def collect(self, target: str) -> CollectionResult:  # pragma: no cover - persist only
        raise NotImplementedError


def _require_isolated_runtime() -> None:
    if os.environ.get("BAOXIAO_DEMO_RUNTIME") != "1":
        raise SystemExit("Refusing demo seed: set BAOXIAO_DEMO_RUNTIME=1.")
    settings = get_settings()
    try:
        url = make_url(settings.database_url)
    except Exception as exc:  # pragma: no cover - defensive operator message
        raise SystemExit("Refusing demo seed: DATABASE_URL is invalid.") from exc
    if not url.drivername.startswith("postgresql") or url.database != DEMO_DATABASE:
        raise SystemExit(
            f"Refusing demo seed: DATABASE_URL must target PostgreSQL database '{DEMO_DATABASE}'."
        )


def _evidence(raw_text: str, value: str) -> list[dict[str, object]]:
    start = raw_text.index(value)
    return [
        {
            "quote": value,
            "page_number": 1,
            "start_offset": start,
            "end_offset": start + len(value),
            "mode": "verbatim",
        }
    ]


def _already_ready(session: object) -> bool:
    source = session.scalar(select(DataSource).where(DataSource.name == DEMO_SOURCE_NAME))
    if source is None:
        return False
    report = KnowledgeIndexService().verify_chunks(session)
    return report.valid and report.active_chunks > 0


def _seed(session: object) -> dict[str, object]:
    source = DataSource(
        name=DEMO_SOURCE_NAME,
        base_url="https://contest-demo.invalid",
        publisher="构造赛事演示数据（非正式）",
        source_type=DataType.REGULATION.value,
        crawl_policy={"constructed": True, "formal_evidence": False},
        rate_limit_seconds=0,
    )
    session.add(source)
    session.commit()
    raw_text = f"{DEMO_TITLE}\n{DEMO_ARTICLE}\n"
    document, created = _DemoCollector().persist(
        session,
        CollectionResult(
            content=raw_text.encode("utf-8"),
            source_url=DEMO_URL,
            final_url=DEMO_URL,
            content_type="text/plain",
            http_status=200,
            title=DEMO_TITLE,
            publisher=source.publisher,
            metadata={
                "constructed_contest_demo": True,
                "not_formal_evidence": True,
                "purpose": "isolated_judge_demo_runtime",
            },
        ),
        DataType.REGULATION.value,
        source_id=source.id,
    )
    if not created:  # guarded database should be empty; keep a clear failure if it is not.
        raise RuntimeError("demo_fixture_hash_already_exists_without_ready_marker")
    ParsingService().parse_document(session, document)
    record = Regulation(
        document_id=document.id,
        title=DEMO_TITLE,
        document_number=None,
        issuing_authority=None,
        effective_date=None,
        expiry_date=None,
        validity_status="unknown",
        article_number=None,
        article_text=DEMO_ARTICLE,
        source_quote=DEMO_ARTICLE,
        field_evidence_json={
            "title": _evidence(raw_text, DEMO_TITLE),
            "article_text": _evidence(raw_text, DEMO_ARTICLE),
        },
        final_review_status=ReviewStatus.PARSED.value,
    )
    session.add(record)
    session.commit()
    validation = ValidationService().validate_document(session, document, record)
    if not validation.valid:
        codes = [issue.code for issue in validation.issues]
        raise RuntimeError(f"demo_fixture_validation_failed:{','.join(codes)}")

    batch = ReviewBatch(
        batch_name="构造赛事演示准入记录（非正式）",
        data_type=DataType.REGULATION.value,
        record_count=1,
        export_path="contest-demo://isolated-runtime",
        schema_version="contest-demo-v1",
        status="completed",
        completed_at=datetime.now(UTC),
    )
    session.add(batch)
    session.flush()
    decision = ReviewDecision(
        batch_id=batch.id,
        record_type=DataType.REGULATION.value,
        record_id=document.id,
        decision=ReviewStatus.APPROVED.value,
        field_reviews_json={"constructed_contest_demo": True, "formal_evidence": False},
        corrections_json={},
        evidence_quality="C",
        review_comment="Isolated constructed fixture for contest demonstration only.",
        reviewer="contest-demo-runtime",
        reviewed_payload_hash=document.sha256,
        schema_version="contest-demo-v1",
    )
    session.add(decision)
    session.flush()
    session.add(
        ReviewBatchItem(
            batch_id=batch.id,
            record_type=DataType.REGULATION.value,
            record_id=document.id,
            document_id=document.id,
            exported_status=document.final_review_status,
            payload_hash=document.sha256,
            decision_id=decision.id,
        )
    )
    StateMachineService.transition_document(
        session,
        document,
        ReviewStatus.APPROVED.value,
        "isolated constructed contest demo fixture",
    )
    document.authenticity_type = AuthenticityType.VERIFIED_PUBLIC.value
    occurrence = document.occurrences[0]
    session.add(
        AuthenticityDecisionLog(
            document_id=document.id,
            previous_authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
            new_authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
            reviewer="contest-demo-runtime",
            review_decision_id=decision.id,
            source_id=source.id,
            verified_occurrence_id=occurrence.id,
            reason="Only for the isolated baoxiao_demo contest runtime; never formal evidence.",
        )
    )
    session.commit()
    index = KnowledgeIndexService()
    indexed = index.index_approved(session)
    if document.id in indexed.rejected:
        raise RuntimeError("demo_fixture_index_rejected:" + ",".join(indexed.rejected[document.id]))
    rebuilt = index.rebuild_all_trusted_chunks(session)
    report = index.verify_chunks(session)
    if not report.valid or report.active_chunks == 0:
        raise RuntimeError("demo_fixture_knowledge_verification_failed:" + ",".join(report.errors))
    return {
        "source_id": source.id,
        "document_id": document.id,
        "record_id": record.id,
        "active_chunks": report.active_chunks,
        "index_run_id": rebuilt.run_id,
    }


def main() -> int:
    _require_isolated_runtime()
    with SessionLocal() as session:
        if _already_ready(session):
            print(json.dumps({"status": "ready", "seeded": False}, ensure_ascii=False))
            return 0
        try:
            result = _seed(session)
        except Exception:
            session.rollback()
            raise
    print(json.dumps({"status": "ready", "seeded": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
