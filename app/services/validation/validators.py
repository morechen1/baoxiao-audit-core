from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EvaluationSample, Penalty, SourceDocument
from app.models.enums import AuthenticityType, DataType


@dataclass
class ValidationIssue:
    validator: str
    code: str
    message: str
    field: str | None = None


@dataclass
class ValidationContext:
    document: SourceDocument | None = None
    record: Any = None
    required_fields: tuple[str, ...] = ()


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)


class Validator(Protocol):
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]: ...


class RequiredFieldValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for name in context.required_fields:
            value = getattr(context.record, name, None)
            if value is None:
                value = getattr(context.document, name, None)
            if value is None or isinstance(value, str) and not value.strip():
                issues.append(
                    ValidationIssue(type(self).__name__, "required", f"{name} is required", name)
                )
        return issues


class SourceUrlValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        document = context.document
        if not document:
            return []
        url = document.source_url or ""
        local = bool(document.metadata_json.get("local_source"))
        if local or url.startswith("file://"):
            return []
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return [
                ValidationIssue(
                    type(self).__name__,
                    "invalid_source",
                    "Public data requires a valid HTTP(S) URL or explicit local source",
                    "source_url",
                )
            ]
        return []


class DateValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        target = context.record
        if target is None:
            return []
        issues: list[ValidationIssue] = []
        for name in ("published_at", "effective_date", "expiry_date", "decision_date"):
            value = getattr(target, name, None)
            if value is not None and not isinstance(value, date):
                issues.append(
                    ValidationIssue(type(self).__name__, "invalid_date", f"{name} is invalid", name)
                )
        return issues


class HashDuplicateValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        document = context.document
        if not document:
            return []
        count = session.scalar(
            select(func.count(SourceDocument.id)).where(SourceDocument.sha256 == document.sha256)
        )
        if count and count > 1:
            return [
                ValidationIssue(
                    type(self).__name__, "duplicate_hash", "Duplicate SHA-256 detected", "sha256"
                )
            ]
        return []


class SourceQuoteValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        record = context.record
        document = context.document
        if record is None or not hasattr(record, "source_quote"):
            return []
        quote = getattr(record, "source_quote", "")
        if not quote:
            return [
                ValidationIssue(
                    type(self).__name__, "missing_quote", "Key extraction requires source_quote"
                )
            ]
        if document and document.raw_text and quote not in document.raw_text:
            return [
                ValidationIssue(
                    type(self).__name__,
                    "quote_not_found",
                    "source_quote is not present in immutable raw text",
                )
            ]
        return []


class AuthenticityValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        target = context.record or context.document
        authenticity = getattr(target, "authenticity_type", None)
        data_type = (
            context.document.data_type
            if context.document
            else DataType.EVALUATION_SAMPLE.value
            if isinstance(target, EvaluationSample)
            else None
        )
        if (
            authenticity == AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value
            and data_type != DataType.EVALUATION_SAMPLE.value
        ):
            return [
                ValidationIssue(
                    type(self).__name__,
                    "constructed_wrong_type",
                    "constructed_for_evaluation is restricted to evaluation_sample",
                    "authenticity_type",
                )
            ]
        return []


class OriginalWordingValidator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        record = context.record
        if isinstance(record, Penalty):
            if not record.original_sales_wording_disclosed and record.original_sales_wording:
                return [
                    ValidationIssue(
                        type(self).__name__,
                        "fabricated_wording",
                        "original_sales_wording must be empty when the source did not disclose it",
                        "original_sales_wording",
                    )
                ]
            if (
                record.original_sales_wording_disclosed
                and record.original_sales_wording
                and context.document
                and record.original_sales_wording not in (context.document.raw_text or "")
            ):
                return [
                    ValidationIssue(
                        type(self).__name__,
                        "wording_not_found",
                        "disclosed original wording must be located in immutable raw text",
                        "original_sales_wording",
                    )
                ]
        return []


class Sha256Validator:
    def validate(self, context: ValidationContext, session: Session) -> list[ValidationIssue]:
        document = context.document
        if document and not re.fullmatch(r"[0-9a-f]{64}", document.sha256 or ""):
            return [
                ValidationIssue(
                    type(self).__name__,
                    "invalid_sha256",
                    "sha256 must be 64 lowercase hexadecimal characters",
                    "sha256",
                )
            ]
        return []
