from app.models import EvaluationSample, Penalty, SourceDocument
from app.models.enums import (
    AuthenticityType,
    DatasetSplit,
    DataType,
    ReviewStatus,
    SampleCategory,
)
from app.services.validation import (
    AuthenticityValidator,
    OriginalWordingValidator,
    ValidationContext,
)


def test_penalty_original_wording_cannot_be_invented(session) -> None:
    penalty = Penalty(
        document_id=1,
        source_entry_index=1,
        source_entry_fingerprint="3" * 64,
        illegal_facts="演示违法事实",
        original_sales_wording_disclosed=False,
        original_sales_wording="不得补写的话术",
        source_quote="演示引用",
        final_review_status=ReviewStatus.PENDING_REVIEW.value,
    )

    issues = OriginalWordingValidator().validate(ValidationContext(record=penalty), session)

    assert issues[0].code == "fabricated_wording"


def test_constructed_authenticity_only_for_evaluation_sample(session) -> None:
    sample = EvaluationSample(
        sample_text="构造样本",
        sample_category=SampleCategory.BOUNDARY.value,
        risk_labels=[],
        expected_evidence={},
        construction_basis="人工构造",
        authenticity_type=AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
        split=DatasetSplit.TRAIN.value,
        final_review_status=ReviewStatus.APPROVED.value,
    )
    document = SourceDocument(
        data_type=DataType.REGULATION.value,
        source_url="file:///demo.txt",
        raw_file_path="/tmp/demo.txt",
        sha256="a" * 64,
        authenticity_type=AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
    )
    validator = AuthenticityValidator()

    assert validator.validate(ValidationContext(record=sample), session) == []
    issues = validator.validate(ValidationContext(document=document), session)
    assert issues[0].code == "constructed_wrong_type"
