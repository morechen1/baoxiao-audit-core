"""Deterministic full-text chunking outside the frozen V1 detector."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import DocumentIngestionError

BOUNDARIES = ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ")


@dataclass(frozen=True)
class DocumentChunk:
    ordinal: int
    start_offset: int
    end_offset: int
    text: str


def segment_long_document(
    raw_text: str,
    *,
    max_chars: int,
    overlap: int,
    max_chunks: int,
) -> list[DocumentChunk]:
    if not raw_text:
        raise DocumentIngestionError("upload_document_empty")
    if len(raw_text) <= max_chars:
        return [DocumentChunk(0, 0, len(raw_text), raw_text)]
    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(raw_text):
        hard_end = min(len(raw_text), start + max_chars)
        end = hard_end
        if hard_end < len(raw_text):
            search_floor = start + max_chars // 2
            candidates = [raw_text.rfind(marker, search_floor, hard_end) for marker in BOUNDARIES]
            boundary = max(candidates)
            if boundary >= search_floor:
                marker = next(
                    value for value in BOUNDARIES if raw_text.startswith(value, boundary)
                )
                end = boundary + len(marker)
        if end <= start:
            raise DocumentIngestionError("long_document_segmentation_failed")
        chunks.append(DocumentChunk(len(chunks), start, end, raw_text[start:end]))
        if len(chunks) > max_chunks:
            raise DocumentIngestionError("long_document_chunk_limit_exceeded")
        if end == len(raw_text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def spans_are_duplicates(
    first_rule: str,
    first_start: int,
    first_end: int,
    second_rule: str,
    second_start: int,
    second_end: int,
) -> bool:
    if first_rule != second_rule:
        return False
    overlap = max(0, min(first_end, second_end) - max(first_start, second_start))
    shortest = min(first_end - first_start, second_end - second_start)
    return bool(shortest > 0 and (overlap == shortest or overlap / shortest >= 0.8))
