from __future__ import annotations

import unicodedata
from dataclasses import dataclass

NORMALIZATION_VERSION = "marketing_text_normalization_v2"


@dataclass(frozen=True)
class NormalizedText:
    text: str
    raw_spans: tuple[tuple[int, int], ...]

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        if start < 0 or end <= start or end > len(self.raw_spans):
            raise ValueError("screening_offset_mapping_failed")
        return self.raw_spans[start][0], self.raw_spans[end - 1][1]


def normalize_marketing_text(raw_text: str) -> NormalizedText:
    output: list[str] = []
    spans: list[tuple[int, int]] = []
    pending_whitespace_start: int | None = None
    pending_whitespace_end = 0
    for raw_start, raw_end, raw_cluster in _unicode_clusters(raw_text):
        expanded = unicodedata.normalize("NFKC", raw_cluster)
        for character in expanded:
            if character.isspace():
                if pending_whitespace_start is None:
                    pending_whitespace_start = raw_start
                pending_whitespace_end = raw_end
                continue
            if pending_whitespace_start is not None and output:
                output.append(" ")
                spans.append((pending_whitespace_start, pending_whitespace_end))
            pending_whitespace_start = None
            lowered = (
                character.lower() if character.isascii() and character.isalpha() else character
            )
            output.append(lowered)
            spans.append((raw_start, raw_end))
    return NormalizedText("".join(output), tuple(spans))


def _unicode_clusters(value: str) -> list[tuple[int, int, str]]:
    """Group a base code point with following combining marks for real NFKC."""
    clusters: list[tuple[int, int, str]] = []
    start = 0
    for index, character in enumerate(value):
        if index and unicodedata.combining(character) == 0:
            clusters.append((start, index, value[start:index]))
            start = index
    if value:
        clusters.append((start, len(value), value[start:]))
    return clusters
