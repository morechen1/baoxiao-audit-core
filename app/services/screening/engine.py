from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.core.exceptions import ScreeningError
from app.services.screening.normalization import normalize_marketing_text
from app.services.screening.rules import MarketingRiskRule, MarketingRuleSet
from app.services.screening.segmenter import SegmentCandidate


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
        findings: list[FindingCandidate] = []
        try:
            for rule in self.ruleset.rules:
                for segment in segments:
                    findings.extend(self._match_segment(raw_text, material_sha256, segment, rule))
        except ScreeningError:
            raise
        except Exception as exc:
            raise ScreeningError("screening_rule_execution_failed") from exc
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
        if rule.required_context_patterns and not any(
            re.search(pattern, normalized.text) for pattern in rule.required_context_patterns
        ):
            return []
        spans = [
            (match.start(), match.end())
            for pattern in rule.positive_patterns
            for match in re.finditer(pattern, normalized.text)
            if not self._excepted(normalized.text, match.start(), match.end(), rule)
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
                "segment_ordinal": segment.ordinal,
                "segment_sha256": segment.segment_sha256,
                "rule_id": rule.rule_id,
                "raw_start_offset": raw_start,
                "raw_end_offset": raw_end,
                "matched_text": matched_text,
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
        text: str,
        start: int,
        end: int,
        rule: MarketingRiskRule,
    ) -> bool:
        context = text[max(0, start - 40) : min(len(text), end + 40)]
        return any(re.search(pattern, context) for pattern in rule.exception_patterns)


def _merge_overlaps(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(spans)):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
