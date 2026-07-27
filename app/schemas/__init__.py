from app.schemas.common import ErrorDetail, ErrorResponse, MessageResponse
from app.schemas.requests import (
    CollectionLocalRequest,
    CollectionUrlRequest,
    ReviewBatchRequest,
    ReviewImportRequest,
    SourceCreate,
    StructuredImportRequest,
)
from app.schemas.structured import (
    PenaltyDraft,
    ProductDocumentDraft,
    RegulationDraft,
    RegulatoryCaseDraft,
    RegulatoryCaseRevision,
    StructuredDraftEnvelope,
)

__all__ = [
    "CollectionLocalRequest",
    "CollectionUrlRequest",
    "ErrorDetail",
    "ErrorResponse",
    "MessageResponse",
    "PenaltyDraft",
    "ProductDocumentDraft",
    "RegulatoryCaseDraft",
    "RegulatoryCaseRevision",
    "RegulationDraft",
    "ReviewBatchRequest",
    "ReviewImportRequest",
    "SourceCreate",
    "StructuredImportRequest",
    "StructuredDraftEnvelope",
]
