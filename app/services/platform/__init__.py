"""Platform-only adapters around the frozen V1 risk detection core."""

from app.services.platform.ingestion import MaterialIngestionService, MaterialInput
from app.services.platform.screening import V1CompatibilityScreeningService

__all__ = [
    "MaterialIngestionService",
    "MaterialInput",
    "V1CompatibilityScreeningService",
]
