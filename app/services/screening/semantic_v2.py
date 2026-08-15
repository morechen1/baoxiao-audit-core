"""Pure Semantic Parser 2.0 chunking, context guards, cache, and arbitration helpers."""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

SEMANTIC_CHUNK_VERSION = "semantic_chunks_v2"
BOUNDARY_RE = re.compile(r"[。！？；\n]")
CONTEXT_BOUNDARIES = "。！？；\n"


@dataclass(frozen=True)
class SemanticTextChunk:
    ordinal: int
    text: str
    document_offset_start: int
    document_offset_end: int


@dataclass(frozen=True)
class SemanticCoverage:
    chunks: tuple[SemanticTextChunk, ...]
    covered_end: int
    partial: bool


def segment_semantic_text(
    raw_text: str,
    *,
    max_chars: int,
    overlap: int,
    max_chunks: int,
    max_text_length: int,
) -> SemanticCoverage:
    """Create bounded exact substrings while preferring paragraph/punctuation boundaries."""
    limit = min(len(raw_text), max_text_length)
    if limit <= max_chars:
        chunk = SemanticTextChunk(0, raw_text[:limit], 0, limit)
        return SemanticCoverage((chunk,), limit, limit < len(raw_text))

    chunks: list[SemanticTextChunk] = []
    cursor = 0
    while cursor < limit and len(chunks) < max_chunks:
        hard_end = min(limit, cursor + max_chars)
        end = hard_end
        if hard_end < limit:
            matches = list(BOUNDARY_RE.finditer(raw_text, cursor, hard_end))
            usable = [match.end() for match in matches if match.end() >= cursor + max_chars // 2]
            if usable:
                end = usable[-1]
        if end <= cursor:
            end = hard_end
        chunks.append(
            SemanticTextChunk(
                ordinal=len(chunks),
                text=raw_text[cursor:end],
                document_offset_start=cursor,
                document_offset_end=end,
            )
        )
        if end >= limit:
            cursor = end
            break
        next_cursor = max(cursor + 1, end - overlap)
        cursor = next_cursor
    covered_end = chunks[-1].document_offset_end if chunks else 0
    return SemanticCoverage(tuple(chunks), covered_end, covered_end < len(raw_text))


def cache_key(*, normalized_text: str, model: str, parser_version: str, schema_version: str) -> str:
    material = "\x1f".join((normalized_text, model, parser_version, schema_version))
    return hashlib.sha256(material.encode()).hexdigest()


class SemanticParserCache:
    """Process-local bounded cache; stores validated JSON only, never credentials or reasoning."""

    def __init__(self) -> None:
        self._values: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def put(self, key: str, value: str, *, max_entries: int) -> None:
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > max_entries:
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


PARSER_CACHE = SemanticParserCache()

EDUCATIONAL_MARKERS = ("请勿相信", "风险提示", "错误话术", "违规话术", "培训材料", "警惕所谓")
PROHIBITIVE_MARKERS = ("不得宣传", "禁止宣称", "严禁承诺", "监管部门明确禁止", "不得承诺")
HISTORICAL_MARKERS = ("历史案例", "曾因", "处罚案例", "以往案件", "案例中")
INTERNAL_MARKERS = (
    "销售人员",
    "业务员",
    "内部员工",
    "佣金",
    "销售业绩",
    "业绩达到",
    "销售指标",
    "考核指标",
    "季度任务",
    "月度任务",
)
QUOTE_INTRO_MARKERS = ("所谓", "例如", "列举", "话术", "宣称", "声称")


def deterministic_context_reject_reason(
    raw_text: str,
    *,
    start: int,
    end: int,
    rule_id: str,
) -> str | None:
    context_start = max(raw_text.rfind(mark, 0, start) for mark in CONTEXT_BOUNDARIES) + 1
    following = [raw_text.find(mark, end) for mark in CONTEXT_BOUNDARIES]
    context_end = min((value for value in following if value >= 0), default=len(raw_text))
    context = raw_text[context_start:context_end]
    if any(marker in context for marker in PROHIBITIVE_MARKERS):
        return "prohibitive_context"
    if any(marker in context for marker in EDUCATIONAL_MARKERS):
        return "educational_context"
    if any(marker in context for marker in HISTORICAL_MARKERS):
        return "historical_context"
    if rule_id in {"extra_contractual_benefit", "false_promotion_or_prize"} and any(
        marker in context for marker in INTERNAL_MARKERS
    ):
        return "internal_incentive_context"
    before = raw_text[max(0, start - 28) : start]
    if any(marker in before for marker in QUOTE_INTRO_MARKERS) and (
        "“" in before or "‘" in before or '"' in before
    ):
        return "quoted_context"
    return None


def spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


ARBITRATION_RULES = {
    "guaranteed_return_or_principal",
    "misleading_interest_or_yield",
    "no_risk_or_no_loss",
}
GUARANTEE_MARKERS = ("保证", "保本", "保息", "稳赚", "固定赚", "收益锁定", "本金安全")
RISK_MARKERS = ("无风险", "零风险", "不亏", "不会损失", "绝对安全", "本金绝对安全")


def arbitration_priority(rule_id: str, quote: str) -> int:
    if rule_id == "guaranteed_return_or_principal":
        return 40 if any(marker in quote for marker in GUARANTEE_MARKERS) else 10
    if rule_id == "no_risk_or_no_loss":
        return 40 if any(marker in quote for marker in RISK_MARKERS) else 10
    if rule_id == "misleading_interest_or_yield":
        return 20
    return 100
