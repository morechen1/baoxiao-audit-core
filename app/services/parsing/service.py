from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import DocumentChunk, SourceDocument
from app.models.enums import ReviewStatus
from app.repositories import DocumentRepository
from app.services.integrity import RawArtifactIntegrityService
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import DocumentParser, ParsedDocument
from app.services.parsing.docx import DocxParser
from app.services.parsing.html import HtmlParser
from app.services.parsing.nfra_json import NfraJsonParser
from app.services.parsing.pdf import PdfParser
from app.services.parsing.text import TextParser
from app.services.state_machine import StateMachineService


class ParsingService:
    parsers: dict[str, DocumentParser] = {
        ".pdf": PdfParser(),
        ".docx": DocxParser(),
        ".html": HtmlParser(),
        ".htm": HtmlParser(),
        ".json": NfraJsonParser(),
        ".txt": TextParser(),
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def parse_document(
        self,
        session: Session,
        document: SourceDocument,
        *,
        transition_status: bool = True,
        commit: bool = True,
    ) -> ParsedDocument:
        RawArtifactIntegrityService(self.settings).verify(document)
        path = Path(document.raw_file_path)
        parser = self.parsers.get(path.suffix.lower())
        if not parser:
            raise ValueError(f"Unsupported document type: {path.suffix}")
        parsed = parser.parse(path)
        metadata = dict(document.metadata_json)
        metadata["parsing"] = {"metadata": parsed.metadata, "warnings": parsed.warnings}
        document.metadata_json = metadata
        if parsed.metadata.get("requires_ocr") is True:
            document.raw_text = None
            document.parse_status = "requires_ocr"
            document.chunks.clear()
            if commit:
                session.commit()
            return parsed
        document.raw_text = parsed.plain_text
        document.source_title = document.source_title or parsed.title
        document.parse_status = "parsed"
        document.chunks.clear()
        offset = 0
        chunk_index = 0
        for page in parsed.pages:
            for text in _chunk_text(page.text):
                start = parsed.plain_text.find(text, offset)
                if start < 0:
                    start = offset
                end = start + len(text)
                document.chunks.append(
                    DocumentChunk(
                        page_number=page.page_number,
                        section_title=None,
                        chunk_index=chunk_index,
                        text=text,
                        start_offset=start,
                        end_offset=end,
                    )
                )
                offset, chunk_index = end, chunk_index + 1
        ParsedArtifactService(self.settings).persist(
            session,
            document,
            parsed,
            parser_name=parser.__class__.__name__,
        )
        if transition_status:
            DocumentRepository(session).transition(document, ReviewStatus.PARSED.value, "parsed")
        if commit:
            session.commit()
        return parsed

    def reparse_document(
        self,
        session: Session,
        document: SourceDocument,
        reason: str,
    ) -> ParsedDocument:
        if document.final_review_status not in {
            ReviewStatus.PARSED.value,
            ReviewStatus.AUTO_VALIDATION_FAILED.value,
        }:
            raise ValueError("document_not_reparseable")
        if not reason.strip():
            raise ValueError("reparse_reason_required")
        for record in StateMachineService.structured_records(session, document):
            session.delete(record)
        metadata = dict(document.metadata_json)
        metadata.pop("automatic_validation", None)
        metadata["reparse_reason"] = reason.strip()
        document.metadata_json = metadata
        previous_status = document.final_review_status
        parsed = self.parse_document(
            session,
            document,
            transition_status=False,
            commit=False,
        )
        if previous_status == ReviewStatus.AUTO_VALIDATION_FAILED.value:
            StateMachineService.transition_document(
                session,
                document,
                ReviewStatus.PARSED.value,
                f"reparsed: {reason.strip()}",
            )
        session.commit()
        return parsed

    def parse_pending(self, session: Session) -> tuple[int, int, list[str]]:
        pending = [
            doc for doc in DocumentRepository(session).list() if doc.parse_status == "pending"
        ]
        count = 0
        requires_ocr = 0
        errors: list[str] = []
        for document in pending:
            try:
                self.parse_document(session, document)
                count += int(document.parse_status == "parsed")
                requires_ocr += int(document.parse_status == "requires_ocr")
            except Exception as exc:
                session.rollback()
                errors.append(f"{document.id}: {exc}")
        return count, requires_ocr, errors


def _chunk_text(text: str, max_chars: int = 1200) -> list[str]:
    chunks: list[str] = []
    paragraphs = [value.strip() for value in text.splitlines() if value.strip()]
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > max_chars:
            chunks.append(current)
            current = ""
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars))
        else:
            current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks
