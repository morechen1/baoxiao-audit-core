"""V1-compatible short and long document orchestration with frozen detector bytes."""

from __future__ import annotations

import time
from typing import Any, cast

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.services.platform.cache import CachingSemanticClaimParser
from app.services.platform.ingestion import MaterialInput
from app.services.platform.long_document import segment_long_document
from app.services.platform.store import RUNTIME_STORE, ChunkRun, PlatformScreeningResult
from app.services.screening.hybrid import HybridScreeningService
from app.services.screening.rules import load_ruleset
from app.services.screening.service import MAX_RAW_TEXT_LENGTH, DeterministicScreeningService

DETECTION_BASELINE = "V1"


class V1CompatibilityScreeningService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.ruleset = load_ruleset()

    def run(self, session: Session, material: MaterialInput) -> PlatformScreeningResult:
        started = time.perf_counter()
        if len(material.raw_text) <= MAX_RAW_TEXT_LENGTH:
            document_chunks = segment_long_document(
                material.raw_text,
                max_chars=MAX_RAW_TEXT_LENGTH,
                overlap=0,
                max_chunks=1,
            )
        else:
            document_chunks = segment_long_document(
                material.raw_text,
                max_chars=self.settings.platform_long_chunk_chars,
                overlap=self.settings.platform_long_chunk_overlap,
                max_chunks=self.settings.platform_max_document_chunks,
            )
        chunk_runs: list[ChunkRun] = []
        provider_calls = 0
        cache_hits = 0
        semantic_statuses: list[str] = []
        for chunk in document_chunks:
            parser = CachingSemanticClaimParser(ruleset=self.ruleset, settings=self.settings)
            deterministic = DeterministicScreeningService(
                ruleset=self.ruleset,
                settings=self.settings,
                semantic_parser=cast(Any, parser),
            )
            service = HybridScreeningService(settings=self.settings, deterministic=deterministic)
            suffix = "" if len(document_chunks) == 1 else f"（分块 {chunk.ordinal + 1}）"
            run = service.run(
                session,
                title=f"{material.title[:270]}{suffix}"[:300],
                material_type=material.material_type,
                raw_text=chunk.text,
                source_label=f"platform:{material.source_format}",
                external_reference=(
                    material.source_filename
                    if len(document_chunks) == 1
                    else f"{material.source_filename}#chunk={chunk.ordinal + 1}"
                )[:255],
            )
            detail = cast(dict[str, Any], service.show(session, run.id))
            evaluation = cast(dict[str, Any], detail.get("evidence_evaluation_summary", {}))
            semantic = cast(
                dict[str, Any], evaluation.get("semantic_parser", {})
            )
            provider_calls += int(semantic.get("provider_calls") or 0)
            semantic_statuses.append(str(semantic.get("status") or "unknown"))
            cache_hits += parser.last_cache_state == "hit"
            chunk_runs.append(
                ChunkRun(
                    ordinal=chunk.ordinal,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    screening_run_id=run.id,
                )
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return RUNTIME_STORE.add_result(
            material,
            chunk_runs,
            {
                "detection_baseline": DETECTION_BASELINE,
                "short_text_direct_v1": len(document_chunks) == 1,
                "document_chunks": len(document_chunks),
                "parser_calls": provider_calls,
                "cache_hits": cache_hits,
                "rag_calls": 0,
                "latency_ms": elapsed_ms,
                "semantic_statuses": semantic_statuses,
                "provider_fail_closed": any(
                    status in {"failed", "partial"} for status in semantic_statuses
                ),
            },
        )
