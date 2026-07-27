from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.enums import APPROVABLE_STATUSES, AuthenticityType, ReviewStatus
from app.repositories import DocumentRepository


class KnowledgeIndexService:
    """Marks trusted records for future search providers; does not create embeddings."""

    def index_approved(self, session: Session) -> int:
        indexed = 0
        repository = DocumentRepository(session)
        for document in repository.list():
            if document.final_review_status not in APPROVABLE_STATUSES:
                continue
            if document.authenticity_type in {
                AuthenticityType.DEMO_ONLY.value,
                AuthenticityType.PENDING_VERIFICATION.value,
            }:
                continue
            if document.knowledge_index_status == "indexed":
                continue
            document.knowledge_index_status = "indexed"
            document.indexed_at = datetime.now(UTC)
            repository.transition(document, ReviewStatus.INDEXED.value, "trusted index marker")
            indexed += 1
        session.commit()
        return indexed
