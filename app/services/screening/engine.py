from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.core.exceptions import ScreeningError
from app.services.screening.normalization import NormalizedText, normalize_marketing_text
from app.services.screening.rules import MarketingRiskRule, MarketingRuleSet
from app.services.screening.segmenter import SegmentCandidate

NEGATION_SCOPE_VERSION = "marketing_claim_negation_scope_v1"
NEGATION_PREFIX_PATTERNS = (
    r"不[\s]*得(?:[\s]*宣[\s]*传)?",
    r"禁[\s]*止",
    r"严[\s]*禁",
    r"切[\s]*勿",
    r"不[\s]*应",
    r"不[\s]*要",
    r"请[\s]*勿(?:[\s]*将)?",
    r"不[\s]*可[\s]*声[\s]*称",
    r"并[\s]*非",
    r"不[\s]*(?:存[\s]*在|承[\s]*诺|保[\s]*证)",
    r"没[\s]*有[\s]*任[\s]*何(?:[\s]*保[\s]*险)?[\s]*产[\s]*品(?:[\s]*都|[\s]*是)?",
)
NEGATION_SUFFIX_PATTERNS = (
    r"(?:的[\s]*)?说[\s]*法(?:[\s]*并)?[\s]*(?:不[\s]*准[\s]*确|错[\s]*误|没[\s]*有[\s]*依[\s]*据)",
    r"并[\s]*非",
    r"不[\s]*等[\s]*于",
    r"不[\s]*同[\s]*于",
    r"不[\s]*代[\s]*表",
)


@dataclass(frozen=True)
class FindingCandidate:
    segment_ordinal: int
    rule_id: str
    category: str
    severity: str
    signal_strength: str
    matched_text: str
    raw_start_offset: int
    raw_end_offset: int
    normalized_match: str
    explanation: str
    review_question: str
    remediation: str
    consumer_notice: str
    finding_sha256: str


class DeterministicComplianceRuleEngine:
    def __init__(self, ruleset: MarketingRuleSet) -> None:
        self.ruleset = ruleset

    def run(
        self,
        *,
        raw_text: str,
        material_sha256: str,
        segments: list[SegmentCandidate],
    ) -> list[FindingCandidate]:
        findings_by_identity: dict[tuple[str, int, int, str], FindingCandidate] = {}
        try:
            for rule in self.ruleset.rules:
                for segment in segments:
                    for candidate in self._match_segment(raw_text, material_sha256, segment, rule):
                        identity = (
                            candidate.rule_id,
                            candidate.raw_start_offset,
                            candidate.raw_end_offset,
                            candidate.normalized_match,
                        )
                        previous = findings_by_identity.get(identity)
                        if previous is None or candidate.segment_ordinal < previous.segment_ordinal:
                            findings_by_identity[identity] = candidate
        except ScreeningError:
            raise
        except Exception as exc:
            raise ScreeningError("screening_rule_execution_failed") from exc
        findings = list(findings_by_identity.values())
        findings.sort(
            key=lambda value: (
                value.raw_start_offset,
                value.raw_end_offset,
                value.rule_id,
                value.finding_sha256,
            )
        )
        return findings

    def _match_segment(
        self,
        raw_text: str,
        material_sha256: str,
        segment: SegmentCandidate,
        rule: MarketingRiskRule,
    ) -> list[FindingCandidate]:
        normalized = normalize_marketing_text(segment.text)
        spans = [
            (match.start(), match.end())
            for pattern in rule.positive_patterns
            for match in re.finditer(pattern, normalized.text)
            if self._required_context_present(
                segment.text,
                normalized,
                match.start(),
                match.end(),
                rule,
            )
            and not self._excepted(
                segment.text,
                normalized,
                match.start(),
                match.end(),
                rule,
            )
        ]
        merged = _merge_overlaps(spans)
        output: list[FindingCandidate] = []
        for normalized_start, normalized_end in merged:
            try:
                relative_start, relative_end = normalized.raw_span(normalized_start, normalized_end)
            except ValueError as exc:
                raise ScreeningError("screening_offset_mapping_failed") from exc
            raw_start = segment.raw_start_offset + relative_start
            raw_end = segment.raw_start_offset + relative_end
            matched_text = raw_text[raw_start:raw_end]
            if matched_text != segment.text[relative_start:relative_end]:
                raise ScreeningError("screening_offset_mapping_failed")
            payload = {
                "material_sha256": material_sha256,
                "ruleset_sha256": self.ruleset.sha256,
                "rule_id": rule.rule_id,
                "raw_start_offset": raw_start,
                "raw_end_offset": raw_end,
                "normalized_match": normalized.text[normalized_start:normalized_end],
            }
            digest = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            output.append(
                FindingCandidate(
                    segment_ordinal=segment.ordinal,
                    rule_id=rule.rule_id,
                    category=rule.category,
                    severity=rule.severity,
                    signal_strength=rule.signal_strength,
                    matched_text=matched_text,
                    raw_start_offset=raw_start,
                    raw_end_offset=raw_end,
                    normalized_match=normalized.text[normalized_start:normalized_end],
                    explanation=rule.explanation_template,
                    review_question=rule.review_question_template,
                    remediation=rule.institution_remediation_template,
                    consumer_notice=rule.consumer_notice_template,
                    finding_sha256=digest,
                )
            )
        return output

    @staticmethod
    def _excepted(
        raw_text: str,
        normalized: NormalizedText,
        start: int,
        end: int,
        rule: MarketingRiskRule,
    ) -> bool:
        raw_start, raw_end = normalized.raw_span(start, end)
        clause_start, clause_end = _local_clause_span(raw_text, raw_start, raw_end, rule)
        clause_raw = raw_text[clause_start:clause_end]
        clause = normalize_marketing_text(clause_raw).text
        relative_start = len(normalize_marketing_text(raw_text[clause_start:raw_start]).text)
        relative_end = relative_start + len(
            normalize_marketing_text(raw_text[raw_start:raw_end]).text
        )
        rule_exception = any(
            _pattern_is_local(pattern, clause, relative_start, relative_end, 40)
            for pattern in rule.exception_patterns
        )
        return rule_exception or _claim_is_negated(clause, relative_start, relative_end, rule)

    @staticmethod
    def _required_context_present(
        raw_text: str,
        normalized: NormalizedText,
        start: int,
        end: int,
        rule: MarketingRiskRule,
    ) -> bool:
        if not rule.required_context_patterns:
            return True
        raw_start, raw_end = normalized.raw_span(start, end)
        clause_start, clause_end = _required_context_clause_span(
            raw_text,
            raw_start,
            raw_end,
            rule,
        )
        clause_raw = raw_text[clause_start:clause_end]
        text = normalize_marketing_text(clause_raw).text
        local_start = len(normalize_marketing_text(raw_text[clause_start:raw_start]).text)
        local_end = local_start + len(normalize_marketing_text(raw_text[raw_start:raw_end]).text)
        for pattern in rule.required_context_patterns:
            for value in re.finditer(pattern, text):
                if value.end() < local_start:
                    distance = local_start - value.end()
                elif value.start() > local_end:
                    distance = value.start() - local_end
                else:
                    distance = 0
                if distance <= rule.context_max_distance:
                    return True
        return False


def _merge_overlaps(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(spans)):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _local_clause_span(
    text: str,
    start: int,
    end: int,
    rule: MarketingRiskRule,
) -> tuple[int, int]:
    markers = [
        r"[。！？；\n\r，,、：:]",
        *map(re.escape, sorted(rule.adversative_boundaries, key=len, reverse=True)),
    ]
    boundaries = list(re.finditer("|".join(markers), text))
    left = 0
    right = len(text)
    for boundary in boundaries:
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return left, right


def _required_context_clause_span(
    text: str,
    start: int,
    end: int,
    rule: MarketingRiskRule,
) -> tuple[int, int]:
    strong_boundary = (
        r"[。！？；]" if rule.required_context_allow_format_newline else r"(?:\r\n|[。！？；\r\n])"
    )
    markers = [
        strong_boundary,
        *map(re.escape, sorted(rule.adversative_boundaries, key=len, reverse=True)),
    ]
    boundaries = list(re.finditer("|".join(markers), text))
    left = 0
    right = len(text)
    for boundary in boundaries:
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return left, right


def _claim_is_negated(
    clause: str,
    match_start: int,
    match_end: int,
    rule: MarketingRiskRule,
) -> bool:
    if rule.claim_negation_scope_version != NEGATION_SCOPE_VERSION:
        raise ScreeningError("screening_ruleset_invalid")
    for pattern in NEGATION_PREFIX_PATTERNS:
        for value in re.finditer(pattern, clause):
            if value.end() <= match_start and match_start - value.end() <= 12:
                return True
    for pattern in NEGATION_SUFFIX_PATTERNS:
        for value in re.finditer(pattern, clause):
            if value.start() >= match_end and value.start() - match_end <= 4:
                return True
            if value.start() <= match_start and value.end() >= match_end:
                return True
    return False


def _pattern_is_local(
    pattern: str,
    clause: str,
    match_start: int,
    match_end: int,
    max_distance: int,
) -> bool:
    for value in re.finditer(pattern, clause):
        if value.end() < match_start:
            distance = match_start - value.end()
        elif value.start() > match_end:
            distance = value.start() - match_end
        else:
            distance = 0
        if distance <= max_distance:
            return True
    return False
