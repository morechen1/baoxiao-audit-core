from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import (
    AUTHENTICITY_TYPE_VALUES,
    DOCUMENT_DATA_TYPE_VALUES,
    HUMAN_REVIEW_STATUS_VALUES,
    KNOWLEDGE_INDEX_STATUS_VALUES,
    REGULATORY_CASE_CATEGORY_VALUES,
    REGULATORY_CASE_USAGE_VALUES,
    REVIEW_STATUS_VALUES,
    AuthenticityType,
    DatasetSplit,
    KnowledgeIndexStatus,
    ReviewStatus,
    SampleCategory,
)


def sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DataSource(TimestampMixin, Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint(
            f"source_type IN ({sql_values(DOCUMENT_DATA_TYPE_VALUES)})",
            name="ck_data_sources_document_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str | None] = mapped_column(String(2048))
    publisher: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    crawl_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rate_limit_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    documents: Mapped[list[SourceDocument]] = relationship(back_populates="source")


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        CheckConstraint(
            f"data_type IN ({sql_values(DOCUMENT_DATA_TYPE_VALUES)})",
            name="ck_source_documents_data_type",
        ),
        CheckConstraint(
            f"authenticity_type IN ({sql_values(AUTHENTICITY_TYPE_VALUES)})",
            name="ck_source_documents_authenticity",
        ),
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_source_documents_review_status",
        ),
        CheckConstraint(
            f"knowledge_index_status IN ({sql_values(KNOWLEDGE_INDEX_STATUS_VALUES)})",
            name="ck_source_documents_index_status",
        ),
        CheckConstraint(
            "collection_status IN ('collected')",
            name="ck_source_documents_collection_status",
        ),
        CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'failed', 'requires_ocr')",
            name="ck_source_documents_parse_status",
        ),
        CheckConstraint(
            "parsed_artifact_sha256 IS NULL OR length(parsed_artifact_sha256) = 64",
            name="ck_source_documents_parsed_artifact_sha256",
        ),
        CheckConstraint(
            "parsed_text_sha256 IS NULL OR length(parsed_text_sha256) = 64",
            name="ck_source_documents_parsed_text_sha256",
        ),
        CheckConstraint(
            "parsed_from_raw_sha256 IS NULL OR length(parsed_from_raw_sha256) = 64",
            name="ck_source_documents_parsed_from_raw_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    data_type: Mapped[str] = mapped_column(String(50), index=True)
    source_url: Mapped[str | None] = mapped_column(String(4096))
    final_url: Mapped[str | None] = mapped_column(String(4096))
    source_title: Mapped[str | None] = mapped_column(String(1000))
    publisher: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[date | None] = mapped_column(Date)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_type: Mapped[str | None] = mapped_column(String(255))
    raw_file_path: Mapped[str] = mapped_column(String(2048))
    raw_text: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    parsed_artifact_path: Mapped[str | None] = mapped_column(String(2048))
    parsed_artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    parsed_text_sha256: Mapped[str | None] = mapped_column(String(64))
    parsed_from_raw_sha256: Mapped[str | None] = mapped_column(String(64))
    parser_name: Mapped[str | None] = mapped_column(String(255))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    authenticity_type: Mapped[str] = mapped_column(
        String(50), default=AuthenticityType.PENDING_VERIFICATION.value
    )
    collection_status: Mapped[str] = mapped_column(String(50), default=ReviewStatus.COLLECTED.value)
    parse_status: Mapped[str] = mapped_column(String(50), default="pending")
    final_review_status: Mapped[str] = mapped_column(
        String(50), default=ReviewStatus.COLLECTED.value, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrected_fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge_index_status: Mapped[str] = mapped_column(
        String(50), default=KnowledgeIndexStatus.NOT_INDEXED.value
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[DataSource | None] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    occurrences: Mapped[list[DocumentOccurrence]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(String(1000))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    document: Mapped[SourceDocument] = relationship(back_populates="chunks")


class Regulation(Base):
    __tablename__ = "regulations"
    __table_args__ = (
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_regulations_review_status",
        ),
        CheckConstraint(
            "validity_status IS NULL OR validity_status = 'unknown'",
            name="ck_regulations_validity_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(1000))
    document_number: Mapped[str | None] = mapped_column(String(255))
    issuing_authority: Mapped[str | None] = mapped_column(String(255))
    effective_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    validity_status: Mapped[str | None] = mapped_column(
        String(50),
        default="unknown",
        server_default="unknown",
    )
    article_number: Mapped[str | None] = mapped_column(String(100))
    article_text: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    field_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    final_review_status: Mapped[str] = mapped_column(String(50))


class Penalty(Base):
    __tablename__ = "penalties"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "source_entry_index",
            name="uq_penalties_document_entry_index",
        ),
        UniqueConstraint(
            "document_id",
            "source_entry_fingerprint",
            name="uq_penalties_document_entry_fingerprint",
        ),
        CheckConstraint(
            "source_entry_index > 0",
            name="ck_penalties_source_entry_index_positive",
        ),
        CheckConstraint(
            "length(source_entry_fingerprint) = 64",
            name="ck_penalties_source_entry_fingerprint_length",
        ),
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_penalties_review_status",
        ),
        CheckConstraint(
            "(original_sales_wording_disclosed = true "
            "AND original_sales_wording IS NOT NULL "
            "AND length(trim(original_sales_wording)) > 0) "
            "OR (original_sales_wording_disclosed = false "
            "AND original_sales_wording IS NULL)",
            name="ck_penalties_original_wording",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    source_entry_index: Mapped[int] = mapped_column()
    source_entry_fingerprint: Mapped[str] = mapped_column(String(64))
    duplicate_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    punished_entity: Mapped[str | None] = mapped_column(String(500))
    authority: Mapped[str | None] = mapped_column(String(255))
    document_number: Mapped[str | None] = mapped_column(String(255))
    decision_date: Mapped[date | None] = mapped_column(Date)
    illegal_facts: Mapped[str] = mapped_column(Text)
    legal_basis: Mapped[str | None] = mapped_column(Text)
    penalty_result: Mapped[str | None] = mapped_column(Text)
    original_sales_wording_disclosed: Mapped[bool] = mapped_column(Boolean, default=False)
    original_sales_wording: Mapped[str | None] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    field_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    final_review_status: Mapped[str] = mapped_column(String(50))


class ProductDocument(Base):
    __tablename__ = "product_documents"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_product_documents_document_id"),
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_product_documents_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    company_name: Mapped[str | None] = mapped_column(String(500))
    product_name: Mapped[str] = mapped_column(String(500))
    product_type: Mapped[str | None] = mapped_column(String(255))
    waiting_period: Mapped[str | None] = mapped_column(Text)
    cooling_off_period: Mapped[str | None] = mapped_column(Text)
    insurance_responsibility: Mapped[str | None] = mapped_column(Text)
    exclusions: Mapped[str | None] = mapped_column(Text)
    cash_value_description: Mapped[str | None] = mapped_column(Text)
    guaranteed_benefit: Mapped[str | None] = mapped_column(Text)
    non_guaranteed_benefit: Mapped[str | None] = mapped_column(Text)
    surrender_risk: Mapped[str | None] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    field_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    final_review_status: Mapped[str] = mapped_column(String(50))


class RegulatoryCase(TimestampMixin, Base):
    __tablename__ = "regulatory_cases"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_regulatory_cases_document_id"),
        CheckConstraint(
            f"case_category IN ({sql_values(REGULATORY_CASE_CATEGORY_VALUES)})",
            name="ck_regulatory_cases_category",
        ),
        CheckConstraint(
            f"case_usage IN ({sql_values(REGULATORY_CASE_USAGE_VALUES)})",
            name="ck_regulatory_cases_usage",
        ),
        CheckConstraint(
            "(marketing_wording_disclosed = true "
            "AND marketing_wording IS NOT NULL "
            "AND length(trim(marketing_wording)) > 0) "
            "OR (marketing_wording_disclosed = false "
            "AND marketing_wording IS NULL)",
            name="ck_regulatory_cases_marketing_wording",
        ),
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_regulatory_cases_review_status",
        ),
        CheckConstraint(
            "evidence_quality IS NULL OR evidence_quality IN ('A', 'B', 'C', 'D')",
            name="ck_regulatory_cases_evidence_quality",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    case_title: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[date | None] = mapped_column(Date, index=True)
    case_category: Mapped[str] = mapped_column(String(50), index=True)
    scenario_text: Mapped[str] = mapped_column(Text)
    marketing_wording_disclosed: Mapped[bool] = mapped_column(Boolean, default=False)
    marketing_wording: Mapped[str | None] = mapped_column(Text)
    case_facts: Mapped[str | None] = mapped_column(Text)
    regulatory_analysis: Mapped[str | None] = mapped_column(Text)
    consumer_advice: Mapped[str | None] = mapped_column(Text)
    case_usage: Mapped[str] = mapped_column(
        String(50),
        default="external_test_candidate",
        server_default="external_test_candidate",
        index=True,
    )
    source_quote: Mapped[str] = mapped_column(Text)
    field_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    final_review_status: Mapped[str] = mapped_column(String(50), index=True)
    evidence_quality: Mapped[str | None] = mapped_column(String(1))


class EvaluationSample(Base):
    __tablename__ = "evaluation_samples"
    __table_args__ = (
        CheckConstraint(
            f"authenticity_type = '{AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value}'",
            name="ck_evaluation_samples_authenticity",
        ),
        CheckConstraint(
            f"sample_category IN ({sql_values(tuple(value.value for value in SampleCategory))})",
            name="ck_evaluation_samples_category",
        ),
        CheckConstraint(
            f"split IN ({sql_values(tuple(value.value for value in DatasetSplit))})",
            name="ck_evaluation_samples_split",
        ),
        CheckConstraint(
            "length(trim(sample_text)) > 0",
            name="ck_evaluation_samples_text_nonempty",
        ),
        CheckConstraint(
            "length(trim(construction_basis)) > 0",
            name="ck_evaluation_samples_basis_nonempty",
        ),
        CheckConstraint(
            f"final_review_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_evaluation_samples_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_text: Mapped[str] = mapped_column(Text)
    sample_category: Mapped[str] = mapped_column(String(50))
    risk_labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    expected_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    construction_basis: Mapped[str] = mapped_column(Text)
    authenticity_type: Mapped[str] = mapped_column(String(50))
    split: Mapped[str] = mapped_column(String(50))
    final_review_status: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewBatch(Base):
    __tablename__ = "review_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('exported', 'completed', 'cancelled')",
            name="ck_review_batches_status",
        ),
        CheckConstraint(
            "export_sha256 IS NULL OR length(export_sha256) = 64",
            name="ck_review_batches_export_sha256",
        ),
        CheckConstraint(
            "bundle_sha256 IS NULL OR length(bundle_sha256) = 64",
            name="ck_review_batches_bundle_sha256",
        ),
        CheckConstraint(
            "bundle_manifest_sha256 IS NULL OR length(bundle_manifest_sha256) = 64",
            name="ck_review_batches_bundle_manifest_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_name: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(50))
    record_count: Mapped[int] = mapped_column(Integer)
    export_path: Mapped[str] = mapped_column(String(2048))
    export_sha256: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(32), default="1.0")
    status: Mapped[str] = mapped_column(String(50), default="exported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    bundle_path: Mapped[str | None] = mapped_column(String(2048))
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    bundle_manifest_sha256: Mapped[str | None] = mapped_column(String(64))


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "evidence_quality IN ('A', 'B', 'C', 'D')",
            name="ck_review_decisions_evidence_quality",
        ),
        CheckConstraint(
            f"decision IN ({sql_values(HUMAN_REVIEW_STATUS_VALUES)})",
            name="ck_review_decisions_decision",
        ),
        CheckConstraint(
            "length(reviewed_payload_hash) = 64",
            name="ck_review_decisions_payload_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("review_batches.id", ondelete="RESTRICT"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(50))
    field_reviews_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrections_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_quality: Mapped[str] = mapped_column(String(1))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(255))
    reviewed_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(50))
    to_status: Mapped[str] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewBatchItem(Base):
    __tablename__ = "review_batch_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "record_type", "record_id", name="uq_review_batch_items_record"
        ),
        CheckConstraint(
            f"exported_status IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_review_batch_items_exported_status",
        ),
        CheckConstraint(
            "length(payload_hash) = 64",
            name="ck_review_batch_items_payload_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("review_batches.id", ondelete="CASCADE"), index=True
    )
    record_type: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    exported_status: Mapped[str] = mapped_column(String(50))
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_decisions.id", ondelete="SET NULL"), unique=True
    )


class DocumentOccurrence(Base):
    __tablename__ = "document_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "source_url",
            "final_url",
            name="uq_document_occurrences_location",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"), index=True
    )
    source_url: Mapped[str | None] = mapped_column(String(4096))
    final_url: Mapped[str | None] = mapped_column(String(4096))
    publisher: Mapped[str | None] = mapped_column(String(255))
    http_status: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    document: Mapped[SourceDocument] = relationship(back_populates="occurrences")


class PilotSourceRegistration(Base):
    __tablename__ = "pilot_source_registrations"
    __table_args__ = (
        UniqueConstraint(
            "source_key",
            "entry_sha256",
            name="uq_pilot_source_registrations_key_hash",
        ),
        UniqueConstraint(
            "source_key",
            "version",
            name="uq_pilot_source_registrations_key_version",
        ),
        CheckConstraint(
            "length(entry_sha256) = 64",
            name="ck_pilot_source_registrations_hash",
        ),
        CheckConstraint(
            "source_type IN ('regulation', 'penalty', 'regulatory_case', 'product_document')",
            name="ck_pilot_source_registrations_type",
        ),
        CheckConstraint(
            "robots_review_status IN ('allowed', 'not_published_manual_review', 'prohibited')",
            name="ck_pilot_source_registrations_robots_status",
        ),
        CheckConstraint(
            "terms_review_status IN ('public_access_allowed', "
            "'not_published_manual_review', 'prohibited')",
            name="ck_pilot_source_registrations_terms_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    data_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), unique=True
    )
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    entry_sha256: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    publisher: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(2048))
    source_type: Mapped[str] = mapped_column(String(50))
    allowed_domains_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    allow_subdomains: Mapped[bool] = mapped_column(Boolean)
    rate_limit_seconds: Mapped[float] = mapped_column(Float)
    max_documents: Mapped[int] = mapped_column(Integer)
    confirmed_by: Mapped[str] = mapped_column(String(255))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str] = mapped_column(String(500))
    robots_review_status: Mapped[str] = mapped_column(String(50))
    robots_checked_at: Mapped[date] = mapped_column(Date)
    robots_checked_by: Mapped[str] = mapped_column(String(255))
    robots_reference_url: Mapped[str | None] = mapped_column(String(2048))
    robots_notes: Mapped[str] = mapped_column(Text)
    terms_review_status: Mapped[str] = mapped_column(String(50))
    terms_checked_at: Mapped[date] = mapped_column(Date)
    terms_checked_by: Mapped[str] = mapped_column(String(255))
    terms_reference_url: Mapped[str | None] = mapped_column(String(2048))
    terms_notes: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text)
    last_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PilotCollectionRun(Base):
    __tablename__ = "pilot_collection_runs"
    __table_args__ = (
        UniqueConstraint("run_uuid", name="uq_pilot_collection_runs_uuid"),
        CheckConstraint(
            "status IN ('validated', 'running', 'completed', "
            "'completed_with_errors', 'failed_configuration', 'cancelled')",
            name="ck_pilot_collection_runs_status",
        ),
        CheckConstraint(
            "length(manifest_set_sha256) = 64",
            name="ck_pilot_collection_runs_manifest_hash",
        ),
        CheckConstraint(
            "length(source_registry_set_sha256) = 64",
            name="ck_pilot_collection_runs_registry_hash",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_uuid: Mapped[str] = mapped_column(String(36))
    selected_manifest: Mapped[str] = mapped_column(String(255))
    manifest_set_sha256: Mapped[str] = mapped_column(String(64))
    source_registry_set_sha256: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50))
    requested_by: Mapped[str] = mapped_column(String(255))
    planned_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)


class PilotCollectionItem(TimestampMixin, Base):
    __tablename__ = "pilot_collection_items"
    __table_args__ = (
        UniqueConstraint("pilot_id", name="uq_pilot_collection_items_pilot_id"),
        CheckConstraint(
            "length(manifest_entry_sha256) = 64",
            name="ck_pilot_collection_items_manifest_hash",
        ),
        CheckConstraint(
            "length(source_registry_entry_sha256) = 64",
            name="ck_pilot_collection_items_registry_hash",
        ),
        CheckConstraint(
            "status IN ('pending', 'attempting', 'collected', 'failed', 'blocked_not_implemented')",
            name="ck_pilot_collection_items_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pilot_collection_runs.id", ondelete="RESTRICT"), index=True
    )
    source_registration_id: Mapped[int] = mapped_column(
        ForeignKey("pilot_source_registrations.id", ondelete="RESTRICT"), index=True
    )
    pilot_id: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(String(4096))
    expected_title: Mapped[str | None] = mapped_column(String(1000))
    evaluation_usage_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    confirmed_by: Mapped[str] = mapped_column(String(255))
    approval_reference: Mapped[str] = mapped_column(String(500))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    manifest_entry_sha256: Mapped[str] = mapped_column(String(64))
    source_registry_entry_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(50))
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    occurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_occurrences.id", ondelete="RESTRICT"), index=True
    )
    document_created: Mapped[bool | None] = mapped_column(Boolean)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthenticityDecisionLog(Base):
    __tablename__ = "authenticity_decision_logs"
    __table_args__ = (
        CheckConstraint(
            f"previous_authenticity_type IN ({sql_values(AUTHENTICITY_TYPE_VALUES)})",
            name="ck_auth_decisions_previous_type",
        ),
        CheckConstraint(
            f"new_authenticity_type = '{AuthenticityType.VERIFIED_PUBLIC.value}'",
            name="ck_auth_decisions_new_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    previous_authenticity_type: Mapped[str] = mapped_column(String(50))
    new_authenticity_type: Mapped[str] = mapped_column(String(50))
    reviewer: Mapped[str] = mapped_column(String(255))
    review_decision_id: Mapped[int] = mapped_column(
        ForeignKey("review_decisions.id", ondelete="RESTRICT"), unique=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="RESTRICT"), index=True
    )
    verified_occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("document_occurrences.id", ondelete="RESTRICT"), unique=True
    )
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ParsedArtifactVersion(Base):
    __tablename__ = "parsed_artifact_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    artifact_path: Mapped[str] = mapped_column(String(2048))
    artifact_sha256: Mapped[str] = mapped_column(String(64), index=True)
    text_sha256: Mapped[str] = mapped_column(String(64))
    from_raw_sha256: Mapped[str] = mapped_column(String(64))
    parser_name: Mapped[str] = mapped_column(String(255))
    parser_version: Mapped[str] = mapped_column(String(32))
    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StructuredDraftRevision(Base):
    __tablename__ = "structured_draft_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), index=True
    )
    record_type: Mapped[str] = mapped_column(String(50))
    structured_record_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    previous_fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    new_fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    previous_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    new_evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewReservation(Base):
    __tablename__ = "review_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'released')",
            name="ck_review_reservations_status",
        ),
        Index(
            "uq_review_reservations_active_record",
            "record_type",
            "record_id",
            unique=True,
            sqlite_where=sql_text("status = 'active'"),
            postgresql_where=sql_text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_type: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="CASCADE"), index=True
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("review_batches.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)
