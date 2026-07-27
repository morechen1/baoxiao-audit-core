import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import ReviewDecisionError
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    EvaluationSample,
    Penalty,
    ProductDocument,
    Regulation,
    ReviewBatchItem,
    ReviewDecision,
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    DatasetSplit,
    DataType,
    ReviewStatus,
    SampleCategory,
)
from app.services.knowledge import KnowledgeIndexService
from app.services.review import ReviewService
from app.services.review.service import payload_hash


def setup_product(session, *, suffix: str = "a"):
    document = SourceDocument(
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url=f"https://example.test/product/{suffix}",
        final_url=f"https://example.test/product/{suffix}",
        raw_file_path=f"/tmp/product-{suffix}.txt",
        raw_text="演示产品，等待期三十日。",
        sha256=suffix * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={
            "automatic_validation": {"valid": True, "issues": []},
            "parsing": {"warnings": []},
        },
    )
    session.add(document)
    session.flush()
    product = ProductDocument(
        document_id=document.id,
        product_name="演示产品",
        waiting_period="待确认",
        source_quote="演示产品",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()
    return document, product


def setup_penalty(session):
    document = SourceDocument(
        data_type=DataType.PENALTY.value,
        source_url="https://example.test/penalty",
        raw_file_path="/tmp/penalty.txt",
        raw_text="演示违法事实，未披露原始话术。",
        sha256="b" * 64,
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    session.add(document)
    session.flush()
    penalty = Penalty(
        document_id=document.id,
        illegal_facts="演示违法事实",
        original_sales_wording_disclosed=False,
        original_sales_wording=None,
        source_quote="演示违法事实",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(penalty)
    session.commit()
    return document, penalty


def export_one(session, tmp_path: Path, data_type: str):
    service = ReviewService(Settings(database_url="sqlite://", data_dir=tmp_path))
    batch = service.export_batch(session, data_type, "jsonl")
    row = json.loads(Path(batch.export_path).read_text(encoding="utf-8").splitlines()[0])
    return service, batch, row


def decision_payload(row, *, status=ReviewStatus.APPROVED.value, corrections=None):
    return {
        "batch_id": row["batch_id"],
        "batch_item_id": row["batch_item_id"],
        "reviewed_payload_hash": payload_hash(row),
        "schema_version": "2.0",
        "record_id": row["record_id"],
        "record_type": row["record_type"],
        "final_status": status,
        "field_reviews": {},
        "corrections": corrections or {},
        "evidence_quality": "A",
        "review_comment": "已核对",
        "reviewer": "reviewer",
    }


def test_review_batch_contains_real_fields_and_full_text(session, tmp_path: Path) -> None:
    document, _ = setup_product(session)

    _, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    assert row["batch_id"] == batch.id
    assert row["document_id"] == document.id
    record = row["parsed_fields"]["records"][0]
    assert record["product_name"] == "演示产品"
    assert isinstance(record["structured_record_id"], int)
    assert row["raw_text"] == document.raw_text
    assert row["raw_text_file"] == document.raw_file_path
    assert batch.export_sha256
    assert batch.schema_version == "2.0"


def test_record_not_in_batch_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session, suffix="a")
    other, _ = setup_product(session, suffix="c")
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["record_id"] = other.id

    with pytest.raises(ReviewDecisionError, match="does not match"):
        service.apply_decision(session, payload, batch.id)


def test_duplicate_batch_item_decision_is_rejected(session, tmp_path: Path) -> None:
    document, product = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    service.apply_decision(session, payload, batch.id)

    assert document.final_review_status == ReviewStatus.APPROVED.value
    assert product.final_review_status == ReviewStatus.APPROVED.value
    assert batch.status == "completed"
    assert batch.completed_at is not None

    with pytest.raises(ReviewDecisionError, match="already has a decision"):
        service.apply_decision(session, payload, batch.id)


def test_payload_hash_change_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    item = session.get(ReviewBatchItem, row["batch_item_id"])
    item.payload_hash = "0" * 64
    session.commit()

    with pytest.raises(ReviewDecisionError, match="reviewed_payload_hash"):
        service.apply_decision(session, decision_payload(row), batch.id)


@pytest.mark.parametrize("field", ["source_quote", "raw_text", "sha256"])
def test_protected_correction_fields_are_rejected(session, tmp_path: Path, field: str) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="Protected correction"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": row["parsed_fields"]["records"][0][
                                "structured_record_id"
                            ],
                            "fields": {field: "forbidden"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_unknown_correction_field_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="Unknown correction"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": row["parsed_fields"]["records"][0][
                                "structured_record_id"
                            ],
                            "fields": {"made_up": "value"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_illegal_penalty_wording_correction_rolls_back(session, tmp_path: Path) -> None:
    document, penalty = setup_penalty(session)
    service, batch, row = export_one(session, tmp_path, DataType.PENALTY.value)

    with pytest.raises(ReviewDecisionError):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": penalty.id,
                            "fields": {"original_sales_wording": "虚构话术"},
                        }
                    ]
                },
            ),
            batch.id,
        )

    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    assert penalty.original_sales_wording is None
    assert session.query(ReviewDecision).count() == 0


def test_failed_post_correction_validation_rolls_back(session, tmp_path: Path) -> None:
    document, product = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)

    with pytest.raises(ReviewDecisionError, match="failed deterministic"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": product.id,
                            "fields": {"product_name": ""},
                        }
                    ]
                },
            ),
            batch.id,
        )

    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    assert product.product_name == "演示产品"
    assert session.query(ReviewDecision).count() == 0


def test_modified_review_package_rejects_entire_import(session, tmp_path: Path) -> None:
    document, _ = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    export_path = Path(batch.export_path)
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    exported["raw_text"] = "tampered"
    export_path.write_text(json.dumps(exported, ensure_ascii=False) + "\n", encoding="utf-8")
    result_path = tmp_path / "result.jsonl"
    result_path.write_text(
        json.dumps(decision_payload(row), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ReviewDecisionError, match="review_package_tampered"):
        service.import_results(session, result_path, batch.id)

    assert document.final_review_status == ReviewStatus.PENDING_REVIEW.value
    assert session.query(ReviewDecision).count() == 0


def test_missing_reviewed_payload_hash_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload.pop("reviewed_payload_hash")

    with pytest.raises(ReviewDecisionError, match="reviewed_payload_hash"):
        service.apply_decision(session, payload, batch.id)


def test_wrong_reviewed_payload_hash_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["reviewed_payload_hash"] = "0" * 64

    with pytest.raises(ReviewDecisionError, match="reviewed_payload_hash"):
        service.apply_decision(session, payload, batch.id)


def test_schema_version_mismatch_is_rejected(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["schema_version"] = "1.0"

    with pytest.raises(ReviewDecisionError, match="schema_version"):
        service.apply_decision(session, payload, batch.id)


@pytest.mark.parametrize(
    "status",
    [
        ReviewStatus.REJECTED.value,
        ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
        ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
    ],
)
def test_non_revision_decisions_cannot_have_corrections(
    session, tmp_path: Path, status: str
) -> None:
    _, product = setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    corrections = {
        "records": [
            {
                "structured_record_id": product.id,
                "fields": {"product_name": "被拒绝的修改"},
            }
        ]
    }

    with pytest.raises(ReviewDecisionError, match="cannot contain corrections"):
        service.apply_decision(
            session,
            decision_payload(row, status=status, corrections=corrections),
            batch.id,
        )
    assert product.product_name == "演示产品"


def setup_regulation(session, *, suffix: str = "r"):
    raw_text = "第一条 原文甲。第二条 原文乙。"
    document = SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url=f"https://example.test/rule/{suffix}",
        final_url=f"https://example.test/rule/{suffix}",
        raw_file_path=f"/tmp/rule-{suffix}.txt",
        raw_text=raw_text,
        sha256=(("d" if suffix == "r" else "e") * 64),
        authenticity_type=AuthenticityType.VERIFIED_PUBLIC.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    session.add(document)
    session.flush()
    first = Regulation(
        document_id=document.id,
        title="规则",
        article_number="第一条",
        article_text="原文甲。",
        source_quote="第一条 原文甲。",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    second = Regulation(
        document_id=document.id,
        title="规则",
        article_number="第二条",
        article_text="原文乙。",
        source_quote="第二条 原文乙。",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add_all([first, second])
    session.commit()
    return document, first, second


def test_multiple_regulations_are_revised_by_structured_id(session, tmp_path: Path) -> None:
    document, first, second = setup_regulation(session)
    service, batch, row = export_one(session, tmp_path, DataType.REGULATION.value)
    corrections = {
        "records": [
            {
                "structured_record_id": first.id,
                "fields": {"article_text": "修订甲"},
            },
            {
                "structured_record_id": second.id,
                "fields": {"article_number": "第二条（修订）"},
            },
        ]
    }
    service.apply_decision(
        session,
        decision_payload(
            row,
            status=ReviewStatus.APPROVED_WITH_REVISION.value,
            corrections=corrections,
        ),
        batch.id,
    )

    assert first.article_text == "修订甲"
    assert second.article_number == "第二条（修订）"
    assert document.metadata_json["post_review_validation"]["valid"] is True


def test_cannot_modify_structured_record_from_other_document(session, tmp_path: Path) -> None:
    _, first, _ = setup_regulation(session, suffix="r")
    _, foreign, _ = setup_regulation(session, suffix="s")
    service, batch, row = export_one(session, tmp_path, DataType.REGULATION.value)
    with pytest.raises(ReviewDecisionError, match="does not belong"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": foreign.id,
                            "fields": {"article_text": "越权修改"},
                        }
                    ]
                },
            ),
            batch.id,
        )


def test_multiple_regulation_revisions_roll_back_together(session, tmp_path: Path) -> None:
    _, first, second = setup_regulation(session)
    service, batch, row = export_one(session, tmp_path, DataType.REGULATION.value)

    with pytest.raises(ReviewDecisionError, match="failed deterministic"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={
                    "records": [
                        {
                            "structured_record_id": first.id,
                            "fields": {"article_text": "本应回滚"},
                        },
                        {
                            "structured_record_id": second.id,
                            "fields": {"article_text": ""},
                        },
                    ]
                },
            ),
            batch.id,
        )

    assert first.article_text == "原文甲。"
    assert second.article_text == "原文乙。"


def setup_evaluation_sample(session):
    sample = EvaluationSample(
        sample_text="评测话术",
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_category", "NOT_A_REAL_CATEGORY"),
        ("split", "INVALID"),
        ("sample_text", "  "),
        ("construction_basis", ""),
        ("risk_labels", ["valid", 123]),
        ("expected_evidence", ["not", "object"]),
    ],
)
def test_invalid_evaluation_revision_is_rejected(
    session, tmp_path: Path, field: str, value: object
) -> None:
    sample = setup_evaluation_sample(session)
    service, batch, row = export_one(session, tmp_path, DataType.EVALUATION_SAMPLE.value)

    with pytest.raises(ReviewDecisionError, match="Invalid evaluation"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={field: value},
            ),
            batch.id,
        )
    assert sample.final_review_status == ReviewStatus.PENDING_REVIEW.value


def test_evaluation_authenticity_cannot_be_revised(session, tmp_path: Path) -> None:
    setup_evaluation_sample(session)
    service, batch, row = export_one(session, tmp_path, DataType.EVALUATION_SAMPLE.value)

    with pytest.raises(ReviewDecisionError, match="Protected correction"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={"authenticity_type": AuthenticityType.VERIFIED_PUBLIC.value},
            ),
            batch.id,
        )


def test_sealed_test_split_cannot_be_reopened(session, tmp_path: Path) -> None:
    sample = setup_evaluation_sample(session)
    sample.split = DatasetSplit.SEALED_TEST.value
    session.commit()
    service, batch, row = export_one(session, tmp_path, DataType.EVALUATION_SAMPLE.value)

    with pytest.raises(ReviewDecisionError, match="sealed_test"):
        service.apply_decision(
            session,
            decision_payload(
                row,
                status=ReviewStatus.APPROVED_WITH_REVISION.value,
                corrections={"split": DatasetSplit.TRAIN.value},
            ),
            batch.id,
        )


def setup_pending_public_product(session):
    source = DataSource(
        name="登记来源",
        base_url="https://source.test",
        source_type=DataType.PRODUCT_DOCUMENT.value,
        enabled=True,
        crawl_policy={"allowed_domains": ["cdn.source.test"]},
    )
    session.add(source)
    session.flush()
    document = SourceDocument(
        source_id=source.id,
        data_type=DataType.PRODUCT_DOCUMENT.value,
        source_url="https://source.test/product",
        final_url="https://cdn.source.test/product",
        raw_file_path="/tmp/public-product.txt",
        raw_text="公开产品条款",
        sha256="9" * 64,
        authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        parse_status="parsed",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
        metadata_json={"automatic_validation": {"valid": True, "issues": []}},
    )
    session.add(document)
    session.flush()
    product = ProductDocument(
        document_id=document.id,
        product_name="公开产品",
        source_quote="公开产品条款",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )
    session.add(product)
    session.commit()
    return document


def test_approved_review_can_audit_verified_public_authenticity(session, tmp_path: Path) -> None:
    document = setup_pending_public_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["authenticity_decision"] = AuthenticityType.VERIFIED_PUBLIC.value
    decision = service.apply_decision(session, payload, batch.id)

    assert document.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value
    log = session.query(AuthenticityDecisionLog).one()
    assert log.document_id == document.id
    assert log.review_decision_id == decision.id
    assert log.reviewer == "reviewer"


def test_rejected_review_cannot_upgrade_authenticity(session, tmp_path: Path) -> None:
    document = setup_pending_public_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row, status=ReviewStatus.REJECTED.value)
    payload["authenticity_decision"] = AuthenticityType.VERIFIED_PUBLIC.value

    with pytest.raises(ReviewDecisionError, match="Only approved"):
        service.apply_decision(session, payload, batch.id)
    assert document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value


def test_pending_authenticity_cannot_index_until_human_verification(
    session, tmp_path: Path
) -> None:
    document = setup_pending_public_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    service.apply_decision(session, decision_payload(row), batch.id)

    first = KnowledgeIndexService().index_approved(session)
    assert "authenticity_not_verified_public" in first.rejected[document.id]


def test_human_verified_approved_document_can_index(session, tmp_path: Path) -> None:
    document = setup_pending_public_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    payload = decision_payload(row)
    payload["authenticity_decision"] = AuthenticityType.VERIFIED_PUBLIC.value
    service.apply_decision(session, payload, batch.id)

    summary = KnowledgeIndexService().index_approved(session)
    assert summary.indexed == 1
    assert document.id not in summary.rejected


def test_result_template_contains_review_binding_fields(session, tmp_path: Path) -> None:
    setup_product(session)
    service, batch, row = export_one(session, tmp_path, DataType.PRODUCT_DOCUMENT.value)
    path = service.create_result_template(session, batch.id)
    template = json.loads(path.read_text(encoding="utf-8"))

    assert path.parent.name == "review_results"
    assert template["batch_id"] == batch.id
    assert template["batch_item_id"] == row["batch_item_id"]
    assert template["reviewed_payload_hash"] == payload_hash(row)
    assert template["schema_version"] == batch.schema_version
