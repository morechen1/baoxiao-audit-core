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
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import (
    AUTHENTICITY_TYPE_VALUES,
    KNOWLEDGE_INDEX_STATUS_VALUES,
    REVIEW_STATUS_VALUES,
    AuthenticityType,
    KnowledgeIndexStatus,
    ReviewStatus,
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
    validity_status: Mapped[str | None] = mapped_column(String(50))
    article_number: Mapped[str | None] = mapped_column(String(100))
    article_text: Mapped[str] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text)
    final_review_status: Mapped[str] = mapped_column(String(50))


class Penalty(Base):
    __tablename__ = "penalties"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_penalties_document_id"),
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
    final_review_status: Mapped[str] = mapped_column(String(50))


class EvaluationSample(Base):
    __tablename__ = "evaluation_samples"
    __table_args__ = (
        CheckConstraint(
            f"authenticity_type IN ({sql_values(AUTHENTICITY_TYPE_VALUES)})",
            name="ck_evaluation_samples_authenticity",
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
            "status IN ('exported', 'completed')",
            name="ck_review_batches_status",
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


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "evidence_quality IN ('A', 'B', 'C', 'D')",
            name="ck_review_decisions_evidence_quality",
        ),
        CheckConstraint(
            f"decision IN ({sql_values(REVIEW_STATUS_VALUES)})",
            name="ck_review_decisions_decision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_batches.id", ondelete="RESTRICT")
    )
    record_type: Mapped[str] = mapped_column(String(50))
    record_id: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(50))
    field_reviews_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrections_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_quality: Mapped[str] = mapped_column(String(1))
    review_comment: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(255))
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
