"""Runtime-only batch orchestration with bounded concurrency and item isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.exceptions import BaoxiaoError
from app.services.platform.ingestion import MaterialInput
from app.services.platform.reports import PlatformReportService
from app.services.platform.screening import V1CompatibilityScreeningService
from app.services.platform.store import RUNTIME_STORE, BatchItem, BatchRecord


class BatchReviewService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create(
        self,
        materials: list[MaterialInput],
        rejected: list[tuple[str, str]],
    ) -> BatchRecord:
        items = [
            BatchItem(ordinal=index, material=material, label=material.source_filename)
            for index, material in enumerate(materials)
        ]
        offset = len(items)
        items.extend(
            BatchItem(
                ordinal=offset + index,
                material=None,
                label=label,
                status="failed",
                error=error,
            )
            for index, (label, error) in enumerate(rejected)
        )
        return RUNTIME_STORE.create_batch(items)

    def process(self, batch_id: str) -> None:
        record = RUNTIME_STORE.batch(batch_id)
        record.status = "processing"
        queued = [item for item in record.items if item.status == "queued"]
        with ThreadPoolExecutor(max_workers=self.settings.platform_batch_concurrency) as pool:
            futures = {pool.submit(self._one, item): item for item in queued}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    item.result_id = future.result()
                    item.status = "success"
                except BaoxiaoError as exc:
                    item.error = str(exc)
                    item.status = "failed"
                except Exception:
                    item.error = "batch_item_failed"
                    item.status = "failed"
        successful = sum(item.status == "success" for item in record.items)
        record.status = "completed" if successful == len(record.items) else "partial"

    @staticmethod
    def _one(item: BatchItem) -> str:
        if item.material is None:
            raise RuntimeError("batch_item_material_missing")
        with SessionLocal() as session:
            result = V1CompatibilityScreeningService().run(session, item.material)
            return result.result_id

    @staticmethod
    def payload(record: BatchRecord) -> dict[str, Any]:
        reports = PlatformReportService()
        rows = []
        successful = 0
        for item in sorted(record.items, key=lambda value: value.ordinal):
            if item.status == "success" and item.result_id:
                with SessionLocal() as session:
                    summary = reports.summary(session, RUNTIME_STORE.result(item.result_id))
                rows.append(summary)
                successful += 1
            else:
                rows.append(
                    {
                        "material": item.label,
                        "title": item.label,
                        "status": item.status,
                        "error": item.error,
                    }
                )
        completed = sum(item.status in {"success", "failed"} for item in record.items)
        total = len(record.items)
        return {
            "batch_id": record.batch_id,
            "status": record.status,
            "progress": round(completed / total * 100) if total else 0,
            "total": total,
            "completed": completed,
            "succeeded": successful,
            "failed": sum(item.status == "failed" for item in record.items),
            "items": rows,
        }
