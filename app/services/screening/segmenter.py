from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.services.screening.normalization import normalize_marketing_text

SEGMENTER_VERSION = "marketing_segmenter_v1"
MAX_SEGMENT_LENGTH = 1200


@dataclass(frozen=True)
class SegmentCandidate:
    ordinal: int
    text: str
    normalized_text: str
    raw_start_offset: int
    raw_end_offset: int
    segment_sha256: str


def segment_marketing_text(raw_text: str, input_sha256: str) -> list[SegmentCandidate]:
    boundaries = [0]
    for match in re.finditer(r"(?:\r?\n\s*\r?\n+)|[。！？；]", raw_text):
        boundaries.append(match.end())
    boundaries.append(len(raw_text))
    spans: list[tuple[int, int]] = []
    for left, right in zip(boundaries, boundaries[1:], strict=False):
        start, end = _trim_span(raw_text, left, right)
        while end - start > MAX_SEGMENT_LENGTH:
            cut = _safe_cut(raw_text, start, min(start + MAX_SEGMENT_LENGTH, end))
            spans.append(_trim_span(raw_text, start, cut))
            start, _ = _trim_span(raw_text, cut, end)
        if end > start:
            spans.append((start, end))
    spans = _attach_short_titles(raw_text, spans)
    candidates: list[SegmentCandidate] = []
    for ordinal, (start, end) in enumerate(spans):
        text = raw_text[start:end]
        normalized = normalize_marketing_text(text).text
        payload = json.dumps(
            {
                "input_sha256": input_sha256,
                "ordinal": ordinal,
                "raw_start_offset": start,
                "raw_end_offset": end,
                "text": text,
                "segmenter_version": SEGMENTER_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        candidates.append(
            SegmentCandidate(
                ordinal=ordinal,
                text=text,
                normalized_text=normalized,
                raw_start_offset=start,
                raw_end_offset=end,
                segment_sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return candidates


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _safe_cut(text: str, start: int, preferred: int) -> int:
    for index in range(preferred, max(start + 1, preferred - 120), -1):
        if text[index - 1].isspace() or text[index - 1] in "，、":
            return index
    return preferred


def _attach_short_titles(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    index = 0
    while index < len(spans):
        start, end = spans[index]
        value = text[start:end]
        if (
            index + 1 < len(spans)
            and len(value) <= 30
            and not any(mark in value for mark in "。！？；")
        ):
            output.append((start, spans[index + 1][1]))
            index += 2
        else:
            output.append((start, end))
            index += 1
    return output
