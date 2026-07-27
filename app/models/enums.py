from enum import StrEnum


class DataType(StrEnum):
    REGULATION = "regulation"
    PENALTY = "penalty"
    PRODUCT_DOCUMENT = "product_document"
    EVALUATION_SAMPLE = "evaluation_sample"


class AuthenticityType(StrEnum):
    VERIFIED_PUBLIC = "verified_public"
    CONSTRUCTED_FOR_EVALUATION = "constructed_for_evaluation"
    DEMO_ONLY = "demo_only"
    PENDING_VERIFICATION = "pending_verification"


class ReviewStatus(StrEnum):
    COLLECTED = "collected"
    PARSED = "parsed"
    AUTO_VALIDATION_FAILED = "auto_validation_failed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    APPROVED_WITH_REVISION = "approved_with_revision"
    REJECTED = "rejected"
    PENDING_SOURCE_VERIFICATION = "pending_source_verification"
    REJECTED_HALLUCINATION = "rejected_hallucination"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_OUTDATED = "rejected_outdated"
    REQUIRES_EXPERT_REVIEW = "requires_expert_review"
    INDEXED = "indexed"


class SampleCategory(StrEnum):
    COMPLIANT = "compliant"
    BOUNDARY = "boundary"
    RISKY = "risky"
    ADVERSARIAL = "adversarial"
    MULTI_RISK = "multi_risk"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    SEALED_TEST = "sealed_test"


APPROVABLE_STATUSES = {
    ReviewStatus.APPROVED.value,
    ReviewStatus.APPROVED_WITH_REVISION.value,
}
