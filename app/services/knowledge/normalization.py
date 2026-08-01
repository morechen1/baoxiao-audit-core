from __future__ import annotations

import re
import unicodedata

NORMALIZATION_VERSION = "trusted_lexical_normalization_v1"
TOKENIZER_VERSION = "han_bigram_lexical_v1"
AUTHORITY_FILTER_NORMALIZATION_VERSION = "trusted_authority_filter_normalization_v1"

_TOKEN_SEGMENT = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[a-z0-9]+(?:[._/-][a-z0-9]+)*")
_DOCUMENT_NUMBER = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]{1,12}\s*"
    r"(?:罚决字|监行处|行处)\s*"
    r"(?:〔|\[)\s*\d{4}\s*(?:〕|\])\s*\d+\s*号"
)
_MONEY = re.compile(r"\d+(?:\.\d+)?(?:万|千|百)?元")
_DATE = re.compile(r"\d{4}(?:-|年)\d{1,2}(?:-|月)\d{1,2}日?")


def trusted_lexical_normalize(value: str) -> str:
    """Normalize retrieval text without changing the immutable source text."""
    normalized = unicodedata.normalize("NFKC", value).lower()
    return " ".join(normalized.split())


def normalize_authority_for_filter(value: str) -> str:
    """Build the versioned authority-only filter value without changing source text."""
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if not character.isspace())


def han_bigram_tokens(value: str) -> list[str]:
    normalized = trusted_lexical_normalize(value)
    ordered: dict[str, None] = {}
    compact = re.sub(r"\s+", "", normalized)
    for match in _DOCUMENT_NUMBER.finditer(normalized):
        ordered.setdefault(re.sub(r"\s+", "", match.group(0)), None)
    for pattern in (_MONEY, _DATE):
        for match in pattern.finditer(compact):
            ordered.setdefault(match.group(0), None)
    for match in _TOKEN_SEGMENT.finditer(normalized):
        segment = match.group(0)
        if all(_is_han(character) for character in segment):
            if len(segment) == 1:
                ordered.setdefault(segment, None)
            else:
                for index in range(len(segment) - 1):
                    ordered.setdefault(segment[index : index + 2], None)
        else:
            ordered.setdefault(segment, None)
    return list(ordered)


def lexical_tokens_text(value: str) -> str:
    return " ".join(han_bigram_tokens(value))


def _is_han(value: str) -> bool:
    return "\u3400" <= value <= "\u4dbf" or "\u4e00" <= value <= "\u9fff"
