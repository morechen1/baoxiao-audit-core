from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceDocument


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_hash(self, sha256: str) -> SourceDocument | None:
        return self.session.scalar(select(SourceDocument).where(SourceDocument.sha256 == sha256))

    def get(self, document_id: int) -> SourceDocument | None:
        return self.session.get(SourceDocument, document_id)

    def list(self, status: str | None = None, data_type: str | None = None) -> list[SourceDocument]:
        statement = select(SourceDocument)
        if status:
            statement = statement.where(SourceDocument.final_review_status == status)
        if data_type:
            statement = statement.where(SourceDocument.data_type == data_type)
        return list(self.session.scalars(statement.order_by(SourceDocument.id)))

    def add(self, document: SourceDocument) -> SourceDocument:
        self.session.add(document)
        self.session.flush()
        return document

    def transition(self, document: SourceDocument, status: str, reason: str | None = None) -> None:
        from app.services.state_machine import StateMachineService

        StateMachineService.transition_document(
            self.session,
            document,
            status,
            reason or "status transition",
        )
