"""Bounded runtime storage for document and batch orchestration; no schema changes."""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.exceptions import PlatformResultError
from app.services.platform.ingestion import MaterialInput


@dataclass(frozen=True)
class ChunkRun:
    ordinal: int
    start_offset: int
    end_offset: int
    screening_run_id: int


@dataclass(frozen=True)
class PlatformScreeningResult:
    result_id: str
    material: MaterialInput
    chunks: tuple[ChunkRun, ...]
    runtime: dict[str, Any]
    created_at: str


@dataclass
class BatchItem:
    ordinal: int
    material: MaterialInput | None
    label: str
    status: str = "queued"
    result_id: str | None = None
    error: str | None = None


@dataclass
class BatchRecord:
    batch_id: str
    items: list[BatchItem]
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class RuntimeStore:
    def __init__(self, max_results: int = 256, max_batches: int = 64) -> None:
        self.max_results = max_results
        self.max_batches = max_batches
        self._lock = threading.RLock()
        self._results: OrderedDict[str, PlatformScreeningResult] = OrderedDict()
        self._batches: OrderedDict[str, BatchRecord] = OrderedDict()

    def add_result(
        self,
        material: MaterialInput,
        chunks: list[ChunkRun],
        runtime: dict[str, Any],
    ) -> PlatformScreeningResult:
        value = PlatformScreeningResult(
            result_id=uuid4().hex,
            material=material,
            chunks=tuple(chunks),
            runtime=runtime,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._results[value.result_id] = value
            while len(self._results) > self.max_results:
                self._results.popitem(last=False)
        return value

    def result(self, result_id: str) -> PlatformScreeningResult:
        with self._lock:
            value = self._results.get(result_id)
            if value is None:
                raise PlatformResultError("platform_result_not_found")
            self._results.move_to_end(result_id)
            return value

    def create_batch(self, items: list[BatchItem]) -> BatchRecord:
        value = BatchRecord(batch_id=uuid4().hex, items=items)
        with self._lock:
            self._batches[value.batch_id] = value
            while len(self._batches) > self.max_batches:
                self._batches.popitem(last=False)
        return value

    def batch(self, batch_id: str) -> BatchRecord:
        with self._lock:
            value = self._batches.get(batch_id)
            if value is None:
                raise PlatformResultError("platform_batch_not_found")
            self._batches.move_to_end(batch_id)
            return value


RUNTIME_STORE = RuntimeStore()
