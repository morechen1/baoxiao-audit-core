from enum import StrEnum


class DataType(StrEnum):
    REGULATION = "regulation"
    PENALTY = "penalty"
    PRODUCT_DOCUMENT = "product_document"
    REGULATORY_CASE = "regulatory_case"
    EVALUATION_SAMPLE = "evaluation_sample"


class DocumentDataType(StrEnum):
    REGULATION = "regulation"
    PENALTY = "penalty"
    PRODUCT_DOCUMENT = "product_document"
    REGULATORY_CASE = "regulatory_case"


class RegulatoryCaseCategory(StrEnum):
    REGULATORY_TYPICAL_CASE = "regulatory_typical_case"
    CONSUMER_RISK_ALERT = "consumer_risk_alert"
    CASE_BASED_EDUCATION = "case_based_education"
    CONSUMER_DISPUTE_CASE = "consumer_dispute_case"
    JUDICIAL_CASE = "judicial_case"


class RegulatoryCaseUsage(StrEnum):
    RETRIEVAL_ONLY = "retrieval_only"
    EXTERNAL_TEST_CANDIDATE = "external_test_candidate"
    SEALED_EXTERNAL_TEST = "sealed_external_test"


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


class KnowledgeIndexStatus(StrEnum):
    NOT_INDEXED = "not_indexed"
    INDEXED = "indexed"
    INDEX_FAILED = "index_failed"


class MarketingMaterialType(StrEnum):
    ADVERTISEMENT = "advertisement"
    SALES_SCRIPT = "sales_script"
    SOCIAL_MEDIA = "social_media"
    SMS = "sms"
    PRODUCT_INTRODUCTION = "product_introduction"
    OTHER = "other"


class ScreeningStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FindingEvidenceStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


class FindingSupportType(StrEnum):
    NORMATIVE_BASIS = "normative_basis"
    ENFORCEMENT_EXAMPLE = "enforcement_example"
    PRODUCT_TERM_CONTEXT = "product_term_context"


class ExplanationAudience(StrEnum):
    INSTITUTION = "institution"
    CONSUMER = "consumer"


class ExplanationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class ExplanationValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED_INVALID_SCHEMA = "rejected_invalid_schema"
    REJECTED_INVALID_CITATION = "rejected_invalid_citation"
    REJECTED_UNSUPPORTED_CLAIM = "rejected_unsupported_claim"
    REJECTED_POLICY_VIOLATION = "rejected_policy_violation"


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

REVIEW_STATUS_VALUES = tuple(status.value for status in ReviewStatus)
HUMAN_REVIEW_STATUS_VALUES = tuple(
    status.value
    for status in ReviewStatus
    if status
    not in {
        ReviewStatus.COLLECTED,
        ReviewStatus.PARSED,
        ReviewStatus.AUTO_VALIDATION_FAILED,
        ReviewStatus.PENDING_REVIEW,
    }
)
AUTHENTICITY_TYPE_VALUES = tuple(value.value for value in AuthenticityType)
KNOWLEDGE_INDEX_STATUS_VALUES = tuple(value.value for value in KnowledgeIndexStatus)
DOCUMENT_DATA_TYPE_VALUES = tuple(value.value for value in DocumentDataType)
REGULATORY_CASE_CATEGORY_VALUES = tuple(value.value for value in RegulatoryCaseCategory)
REGULATORY_CASE_USAGE_VALUES = tuple(value.value for value in RegulatoryCaseUsage)
MARKETING_MATERIAL_TYPE_VALUES = tuple(value.value for value in MarketingMaterialType)
SCREENING_STATUS_VALUES = tuple(value.value for value in ScreeningStatus)
FINDING_EVIDENCE_STATUS_VALUES = tuple(value.value for value in FindingEvidenceStatus)
FINDING_SUPPORT_TYPE_VALUES = tuple(value.value for value in FindingSupportType)
EXPLANATION_AUDIENCE_VALUES = tuple(value.value for value in ExplanationAudience)
EXPLANATION_RUN_STATUS_VALUES = tuple(value.value for value in ExplanationRunStatus)
EXPLANATION_VALIDATION_STATUS_VALUES = tuple(value.value for value in ExplanationValidationStatus)
