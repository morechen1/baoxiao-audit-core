from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import AuthenticityType, ReviewStatus


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
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
    collection_status: Mapped[str] = mapped_column(
        String(50), default=ReviewStatus.COLLECTED.value
    )
    parse_status: Mapped[str] = mapped_column(String(50), default="pending")
    final_review_status: Mapped[str] = mapped_column(
        String(50), default=ReviewStatus.COLLECTED.value, index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    corrected_fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge_index_status: Mapped[str] = mapped_column(String(50), default="not_indexed")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[DataSource | None] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_documents.id"), index=True)
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

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_name: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(50))
    record_count: Mapped[int] = mapped_column(Integer)
    export_path: Mapped[str] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(50), default="exported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("review_batches.id"))
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
