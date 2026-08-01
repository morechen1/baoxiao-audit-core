from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import TrustGateError
from app.models import KnowledgeChunk, SourceDocument
from app.models.enums import APPROVABLE_STATUSES, AuthenticityType, DataType, KnowledgeIndexStatus
from app.services.knowledge.chunks import RANKING_VERSION
from app.services.knowledge.materialization import MATERIALIZABLE_TYPES
from app.services.knowledge.normalization import han_bigram_tokens, trusted_lexical_normalize

MAX_QUERY_LENGTH = 500
MAX_LIMIT = 100
MAX_OFFSET = 10_000


@dataclass(frozen=True)
class SearchRequest:
    query: str = ""
    record_types: tuple[str, ...] = ()
    pilot_ids: tuple[str, ...] = ()
    authority: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    evidence_quality: tuple[str, ...] = ()
    limit: int = 20
    offset: int = 0

    def __post_init__(self) -> None:
        if len(self.query) > MAX_QUERY_LENGTH:
            raise TrustGateError("knowledge_search_query_too_long")
        if not 1 <= self.limit <= MAX_LIMIT:
            raise TrustGateError("knowledge_search_limit_invalid")
        if not 0 <= self.offset <= MAX_OFFSET:
            raise TrustGateError("knowledge_search_offset_invalid")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise TrustGateError("knowledge_search_date_range_invalid")
        if set(self.record_types) - MATERIALIZABLE_TYPES:
            raise TrustGateError("knowledge_search_record_type_invalid")
        if set(self.evidence_quality) - {"A", "B", "C", "D"}:
            raise TrustGateError("knowledge_search_evidence_quality_invalid")


@dataclass(frozen=True)
class SearchResult:
    score: float
    rank: int
    record_type: str
    pilot_id: str | None
    source_document_id: int
    structured_record_id: int
    portable_record_key: str | None
    chunk_kind: str
    chunk_ordinal: int
    title: str
    snippet: str
    matched_terms: list[str]
    authority: str | None
    relevant_date: date | None
    evidence_quality: str | None
    authenticity_status: str
    review_status: str
    source_locator: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    source_url: str
    chunk_content_sha256: str
    chunk_identity_sha256: str
    ranking_version: str = RANKING_VERSION

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TrustedKnowledgeSearchService:
    def search(self, session: Session, request: SearchRequest) -> list[SearchResult]:
        normalized_query = trusted_lexical_normalize(request.query)
        query_tokens = han_bigram_tokens(request.query)
        statement = (
            select(KnowledgeChunk)
            .join(SourceDocument, SourceDocument.id == KnowledgeChunk.source_document_id)
            .where(
                KnowledgeChunk.is_active.is_(True),
                KnowledgeChunk.authenticity_status == AuthenticityType.VERIFIED_PUBLIC.value,
                KnowledgeChunk.review_status.in_(APPROVABLE_STATUSES),
                KnowledgeChunk.record_type.in_(MATERIALIZABLE_TYPES),
                SourceDocument.authenticity_type == AuthenticityType.VERIFIED_PUBLIC.value,
                SourceDocument.final_review_status.in_(APPROVABLE_STATUSES),
                SourceDocument.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value,
                SourceDocument.data_type != DataType.REGULATORY_CASE.value,
            )
        )
        if request.record_types:
            statement = statement.where(KnowledgeChunk.record_type.in_(request.record_types))
        if request.pilot_ids:
            statement = statement.where(KnowledgeChunk.pilot_id.in_(request.pilot_ids))
        if request.authority:
            statement = statement.where(
                func.lower(KnowledgeChunk.authority).contains(request.authority.lower())
            )
        if request.date_from:
            statement = statement.where(KnowledgeChunk.relevant_date >= request.date_from)
        if request.date_to:
            statement = statement.where(KnowledgeChunk.relevant_date <= request.date_to)
        if request.evidence_quality:
            statement = statement.where(
                KnowledgeChunk.evidence_quality.in_(request.evidence_quality)
            )
        dialect = session.get_bind().dialect.name
        if normalized_query and dialect == "postgresql":
            vector = func.to_tsvector("simple", KnowledgeChunk.lexical_tokens)
            lexical_matches = [
                vector.op("@@")(func.plainto_tsquery("simple", token)) for token in query_tokens
            ]
            statement = statement.where(
                or_(
                    *lexical_matches,
                    func.similarity(KnowledgeChunk.normalized_text, normalized_query) > 0.05,
                    KnowledgeChunk.normalized_text.contains(normalized_query),
                )
            )
        chunks = list(session.scalars(statement.order_by(KnowledgeChunk.id).limit(5000)))
        scored = []
        for chunk in chunks:
            score, matched = _score(chunk, normalized_query, query_tokens)
            if normalized_query and not matched and normalized_query not in chunk.normalized_text:
                continue
            scored.append((score, chunk, matched))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].pilot_id or "",
                item[1].record_type,
                item[1].chunk_ordinal,
                item[1].chunk_identity_sha256,
            )
        )
        page = scored[request.offset : request.offset + request.limit]
        return [
            SearchResult(
                score=score,
                rank=request.offset + index,
                record_type=chunk.record_type,
                pilot_id=chunk.pilot_id,
                source_document_id=chunk.source_document_id,
                structured_record_id=chunk.structured_record_id,
                portable_record_key=chunk.portable_record_key,
                chunk_kind=chunk.chunk_kind,
                chunk_ordinal=chunk.chunk_ordinal,
                title=chunk.title,
                snippet=_snippet(chunk.text, normalized_query, matched),
                matched_terms=matched,
                authority=chunk.authority,
                relevant_date=chunk.relevant_date,
                evidence_quality=chunk.evidence_quality,
                authenticity_status=chunk.authenticity_status,
                review_status=chunk.review_status,
                source_locator=chunk.source_locator_json,
                evidence_references=chunk.evidence_reference_json,
                source_url=chunk.source_url,
                chunk_content_sha256=chunk.chunk_content_sha256,
                chunk_identity_sha256=chunk.chunk_identity_sha256,
            )
            for index, (score, chunk, matched) in enumerate(page, start=1)
        ]


def _score(
    chunk: KnowledgeChunk, normalized_query: str, query_tokens: list[str]
) -> tuple[float, list[str]]:
    chunk_tokens = set(chunk.lexical_tokens.split())
    matched = [token for token in query_tokens if token in chunk_tokens]
    coverage = len(set(matched)) / len(set(query_tokens)) if query_tokens else 0.0
    trigram = (
        _trigram_similarity(normalized_query, chunk.normalized_text) if normalized_query else 0.0
    )
    title_bonus = (
        1.0
        if normalized_query and normalized_query in trusted_lexical_normalize(chunk.title)
        else 0.0
    )
    document_number = trusted_lexical_normalize(
        str(chunk.source_locator_json.get("document_number") or "")
    )
    entity = trusted_lexical_normalize(str(chunk.source_locator_json.get("punished_entity") or ""))
    document_number_bonus = 1.0 if normalized_query and normalized_query == document_number else 0.0
    entity_bonus = 1.0 if normalized_query and normalized_query in entity else 0.0
    quality_bonus = {"A": 0.10, "B": 0.07, "C": 0.03, "D": 0.0}.get(
        chunk.evidence_quality or "", 0.0
    )
    score = (
        2.0 * coverage
        + trigram
        + 0.50 * title_bonus
        + 0.50 * document_number_bonus
        + 0.35 * entity_bonus
        + quality_bonus
    )
    return round(score, 8), matched


def _trigram_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_grams = _trigrams(left)
    right_grams = _trigrams(right)
    if not left_grams or not right_grams:
        return 1.0 if left == right else 0.0
    return 2.0 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams))


def _trigrams(value: str) -> set[str]:
    compact = value.replace(" ", "")
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[index : index + 3] for index in range(len(compact) - 2)}


def _snippet(text: str, normalized_query: str, matched_terms: list[str], limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    needles = [normalized_query, *matched_terms]
    lower = text.lower()
    positions = [lower.find(needle) for needle in needles if needle and lower.find(needle) >= 0]
    start = max(0, (min(positions) if positions else 0) - 60)
    return text[start : start + limit]
