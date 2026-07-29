import hashlib
import json
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.exceptions import ReviewDecisionError
from app.models import (
    EvaluationSample,
    ProductDocument,
    ReviewBatch,
    ReviewBatchItem,
    ReviewDecision,
    SourceDocument,
)
from app.models.entities import Base
from app.models.enums import (
    AuthenticityType,
    DatasetSplit,
    DataType,
    ReviewStatus,
    SampleCategory,
)
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.review import ReviewService
from app.services.review.service import payload_hash


@pytest.fixture
def no_autoflush_session(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with factory() as session:
        session.info["data_dir"] = tmp_path / "data"
        assert session.autoflush is False
        yield session
    Base.metadata.drop_all(engine)


def add_product(session: Session, suffix: str) -> SourceDocument:
    raw_text = f"产品{suffix}条款"
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url=f"https://example.test/products/{suffix}",
        final_url=f"https://example.test/products/{suffix}",
        raw_file_path=f"pending-{suffix}",
        raw_text=raw_text,
        sha256=hashlib.sha256(f"pending-{suffix}".encode()).hexdigest(),
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    session.add(document)
    session.flush()
    session.add(
        ProductDocument(
            document_id=document.id,
            product_name=raw_text,
            source_quote=raw_text,
            field_evidence_json={},
            final_review_status=ReviewStatus.PENDING_REVIEW.value,
        )
    )
    session.commit()
    return document


def materialize_products(session: Session) -> ReviewService:
    data_dir = session.info["data_dir"]
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(database_url="sqlite://", data_dir=data_dir)
    for document in session.query(SourceDocument).order_by(SourceDocument.id):
        content = f"{document.raw_text}\nartifact={document.id}".encode()
        digest = hashlib.sha256(content).hexdigest()
        raw_path = raw_dir / f"{digest}.txt"
        raw_path.write_bytes(content)
        document.sha256 = digest
        document.raw_file_path = str(raw_path)
        ParsedArtifactService(settings).persist(
            session,
            document,
            ParsedDocument(
                title=document.raw_text,
                plain_text=document.raw_text or "",
                pages=[ParsedPage(page_number=1, text=document.raw_text or "")],
            ),
            parser_name="TestParser",
        )
        product = session.query(ProductDocument).filter_by(document_id=document.id).one()
        product.field_evidence_json = {
            "product_name": [
                {
                    "quote": product.product_name,
                    "page_number": 1,
                    "start_offset": 0,
                    "end_offset": len(product.product_name),
                    "mode": "verbatim",
                }
            ]
        }
    session.commit()
    return ReviewService(settings)


def export_rows(
    session: Session,
    service: ReviewService,
    data_type: str,
) -> tuple[ReviewBatch, list[dict[str, object]]]:
    batch = service.export_batch(session, data_type, "jsonl")
    rows = [
        json.loads(line)
        for line in Path(batch.export_path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return batch, rows


def decision(row: dict[str, object]) -> dict[str, object]:
    return {
        "batch_id": row["batch_id"],
        "batch_item_id": row["batch_item_id"],
        "reviewed_payload_hash": payload_hash(row),
        "review_payload_schema_version": row["review_payload_schema_version"],
        "schema_version": "2.0",
        "record_id": row["record_id"],
        "record_type": row["record_type"],
        "final_status": ReviewStatus.APPROVED.value,
        "field_reviews": {},
        "corrections": {},
        "evidence_quality": "A",
        "review_comment": "autoflush false completion test",
        "reviewer": "tester",
    }


def add_evaluation_sample(session: Session, suffix: str = "a") -> EvaluationSample:
    sample = EvaluationSample(
        sample_text=f"评测话术{suffix}",
        sample_category=SampleCategory.RISKY.value,
        risk_labels=["夸大"],
        expected_evidence={"must_include": ["条款"]},
        construction_basis="根据风险模式人工构造",
        authenticity_type=AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
        split=DatasetSplit.TRAIN.value,
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(sample)
    session.commit()
    return sample


def test_single_document_decision_completes_batch_with_timestamp(
    no_autoflush_session: Session,
) -> None:
    add_product(no_autoflush_session, "one")
    service = materialize_products(no_autoflush_session)
    batch, rows = export_rows(no_autoflush_session, service, DataType.PRODUCT_DOCUMENT.value)

    service.apply_decision(no_autoflush_session, decision(rows[0]), batch.id)

    assert batch.status == "completed"
    assert batch.completed_at is not None


def test_multi_document_batch_stays_exported_until_last_decision(
    no_autoflush_session: Session,
) -> None:
    add_product(no_autoflush_session, "first")
    add_product(no_autoflush_session, "second")
    service = materialize_products(no_autoflush_session)
    batch, rows = export_rows(no_autoflush_session, service, DataType.PRODUCT_DOCUMENT.value)

    service.apply_decision(no_autoflush_session, decision(rows[0]), batch.id)
    assert batch.status == "exported"
    assert batch.completed_at is None

    service.apply_decision(no_autoflush_session, decision(rows[1]), batch.id)
    assert batch.status == "completed"
    assert batch.completed_at is not None


def test_evaluation_sample_batch_uses_same_completion_rule(
    no_autoflush_session: Session,
) -> None:
    add_evaluation_sample(no_autoflush_session)
    service = ReviewService(
        Settings(
            database_url="sqlite://",
            data_dir=no_autoflush_session.info["data_dir"],
        )
    )
    batch, rows = export_rows(no_autoflush_session, service, DataType.EVALUATION_SAMPLE.value)

    service.apply_decision(no_autoflush_session, decision(rows[0]), batch.id)

    assert batch.status == "completed"
    assert batch.completed_at is not None


def test_bulk_import_completes_only_after_all_items(
    no_autoflush_session: Session,
    tmp_path: Path,
) -> None:
    add_product(no_autoflush_session, "bulk-a")
    add_product(no_autoflush_session, "bulk-b")
    service = materialize_products(no_autoflush_session)
    batch, rows = export_rows(no_autoflush_session, service, DataType.PRODUCT_DOCUMENT.value)
    result_path = tmp_path / "bulk-results.jsonl"
    result_path.write_text(
        "".join(json.dumps(decision(row)) + "\n" for row in rows),
        encoding="utf-8",
    )

    imported, errors = service.import_results(no_autoflush_session, result_path, batch.id)

    assert imported == 2
    assert errors == []
    assert batch.status == "completed"
    assert batch.completed_at is not None


def test_completion_flush_remains_rollback_safe(
    no_autoflush_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = add_product(no_autoflush_session, "rollback")
    service = materialize_products(no_autoflush_session)
    batch, rows = export_rows(no_autoflush_session, service, DataType.PRODUCT_DOCUMENT.value)
    original = ReviewService._complete_batch_if_ready

    def fail_after_completion(session: Session, target: ReviewBatch) -> None:
        original(session, target)
        raise ReviewDecisionError("forced_failure_after_completion")

    monkeypatch.setattr(
        ReviewService,
        "_complete_batch_if_ready",
        staticmethod(fail_after_completion),
    )

    with pytest.raises(ReviewDecisionError, match="forced_failure_after_completion"):
        service.apply_decision(no_autoflush_session, decision(rows[0]), batch.id)

    no_autoflush_session.expire_all()
    assert no_autoflush_session.get(ReviewBatch, batch.id).status == "exported"
    assert no_autoflush_session.get(ReviewBatch, batch.id).completed_at is None
    assert no_autoflush_session.get(ReviewBatchItem, rows[0]["batch_item_id"]).decision_id is None
    assert (
        no_autoflush_session.get(SourceDocument, document.id).final_review_status
        == ReviewStatus.PENDING_REVIEW.value
    )
    assert no_autoflush_session.query(ReviewDecision).count() == 0


def test_completed_and_cancelled_batches_remain_closed(
    no_autoflush_session: Session,
    tmp_path: Path,
) -> None:
    add_product(no_autoflush_session, "completed")
    service = materialize_products(no_autoflush_session)
    completed, rows = export_rows(no_autoflush_session, service, DataType.PRODUCT_DOCUMENT.value)
    service.apply_decision(no_autoflush_session, decision(rows[0]), completed.id)
    duplicate_path = tmp_path / "duplicate.jsonl"
    duplicate_path.write_text(json.dumps(decision(rows[0])) + "\n", encoding="utf-8")

    imported, errors = service.import_results(no_autoflush_session, duplicate_path, completed.id)
    assert imported == 0
    assert "already has a decision" in errors[0]
    assert completed.status == "completed"

    add_evaluation_sample(no_autoflush_session, "cancelled")
    cancelled, _ = export_rows(no_autoflush_session, service, DataType.EVALUATION_SAMPLE.value)
    service.cancel_batch(no_autoflush_session, cancelled.id, "no longer required")
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None
