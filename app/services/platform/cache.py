"""Exact-input cache wrapper for the unchanged V1 Semantic Parser interface."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict

from app.core.config import Settings, get_settings
from app.services.screening.engine import FindingCandidate
from app.services.screening.rules import MarketingRuleSet
from app.services.screening.segmenter import SegmentCandidate
from app.services.screening.semantic_parser import (
    SEMANTIC_PARSER_VERSION,
    SemanticClaimOutput,
    SemanticClaimParser,
    SemanticParserOutcome,
)

V1_SEMANTIC_PARSER_BLOB = "404ad37513564abf0ae3c39340fedfbecb1f4639"


class ExactSemanticResultCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: OrderedDict[str, SemanticParserOutcome] = OrderedDict()

    def get(self, key: str) -> SemanticParserOutcome | None:
        with self._lock:
            value = self._values.get(key)
            if value is None:
                return None
            self._values.move_to_end(key)
            return copy.deepcopy(value)

    def put(self, key: str, value: SemanticParserOutcome, max_entries: int) -> None:
        with self._lock:
            self._values[key] = copy.deepcopy(value)
            self._values.move_to_end(key)
            while len(self._values) > max_entries:
                self._values.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


EXACT_SEMANTIC_CACHE = ExactSemanticResultCache()


class CachingSemanticClaimParser:
    """Delegates all parsing/validation to V1 and caches only its exact completed outcome."""

    def __init__(
        self,
        *,
        ruleset: MarketingRuleSet,
        settings: Settings | None = None,
        parser: SemanticClaimParser | None = None,
        cache: ExactSemanticResultCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.parser = parser or SemanticClaimParser(ruleset=ruleset, settings=self.settings)
        self.cache = cache or EXACT_SEMANTIC_CACHE
        self.last_cache_state = "disabled"

    def supplement(
        self,
        *,
        raw_text: str,
        material_sha256: str,
        segments: list[SegmentCandidate],
        deterministic: list[FindingCandidate],
    ) -> SemanticParserOutcome:
        if (
            not self.settings.semantic_parser_enabled
            or not self.settings.semantic_parser_cache_enabled
        ):
            self.last_cache_state = "disabled"
            return self.parser.supplement(
                raw_text=raw_text,
                material_sha256=material_sha256,
                segments=segments,
                deterministic=deterministic,
            )
        key = self._key(raw_text)
        cached = self.cache.get(key)
        if cached is not None:
            self.last_cache_state = "hit"
            return cached
        self.last_cache_state = "miss"
        outcome = self.parser.supplement(
            raw_text=raw_text,
            material_sha256=material_sha256,
            segments=segments,
            deterministic=deterministic,
        )
        if outcome.diagnostics.get("status") == "completed":
            self.cache.put(key, outcome, self.settings.semantic_parser_cache_entries)
        return outcome

    def _key(self, raw_text: str) -> str:
        schema = json.dumps(
            SemanticClaimOutput.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = {
            "text_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
            "model": self.settings.llm_model,
            "parser_version": SEMANTIC_PARSER_VERSION,
            "parser_blob": V1_SEMANTIC_PARSER_BLOB,
            "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
