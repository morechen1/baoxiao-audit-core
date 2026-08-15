"""In-memory, allowlisted document ingestion that preserves extracted wording."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.config import Settings, get_settings
from app.core.exceptions import DocumentIngestionError

ALLOWED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf"}
ALLOWED_CONTENT_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".pdf": {"application/pdf", "application/octet-stream"},
}


@dataclass(frozen=True)
class MaterialInput:
    title: str
    material_type: str
    raw_text: str
    source_filename: str
    source_format: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MaterialIngestionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def ingest(
        self,
        *,
        filename: str,
        content: bytes,
        material_type: str,
        content_type: str | None = None,
    ) -> MaterialInput:
        safe_name = self.safe_filename(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentIngestionError("upload_extension_not_allowed")
        self.validate_content_type(safe_name, content_type)
        if not content or len(content) > self.settings.max_upload_bytes:
            raise DocumentIngestionError("upload_size_invalid")
        if extension in {".txt", ".md"}:
            text = self._decode_text(content)
        elif extension == ".docx":
            text = self._docx(content)
        else:
            text = self._pdf(content)
        if not text.strip():
            raise DocumentIngestionError("upload_document_empty")
        return MaterialInput(
            title=Path(safe_name).stem[:300] or "上传材料",
            material_type=material_type,
            raw_text=text,
            source_filename=safe_name,
            source_format=extension.removeprefix("."),
            metadata={"byte_size": len(content), "character_count": len(text)},
        )

    @staticmethod
    def safe_filename(filename: str) -> str:
        if not filename or "\x00" in filename:
            raise DocumentIngestionError("upload_filename_invalid")
        normalized = filename.replace("\\", "/")
        safe_name = Path(normalized).name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise DocumentIngestionError("upload_filename_invalid")
        return safe_name[:255]

    @staticmethod
    def validate_content_type(filename: str, content_type: str | None) -> None:
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentIngestionError("upload_extension_not_allowed")
        normalized = (content_type or "application/octet-stream").split(";", 1)[0].lower()
        if normalized not in ALLOWED_CONTENT_TYPES[extension]:
            raise DocumentIngestionError("upload_content_type_not_allowed")

    @staticmethod
    def _decode_text(content: bytes) -> str:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentIngestionError("upload_text_encoding_invalid") from exc
        if "\x00" in text:
            raise DocumentIngestionError("upload_text_encoding_invalid")
        return text

    @staticmethod
    def _docx(content: bytes) -> str:
        if not content.startswith(b"PK"):
            raise DocumentIngestionError("upload_docx_invalid")
        try:
            document = Document(io.BytesIO(content))
        except Exception as exc:
            raise DocumentIngestionError("upload_docx_invalid") from exc
        values: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                text = Paragraph(child, document).text.strip()
                if text:
                    values.append(text)
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        values.append("\t".join(cells))
        return "\n".join(values)

    @staticmethod
    def _pdf(content: bytes) -> str:
        if not content.lstrip().startswith(b"%PDF"):
            raise DocumentIngestionError("upload_pdf_invalid")
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                pages = [page.get_text("text").strip() for page in document]
        except Exception as exc:
            raise DocumentIngestionError("upload_pdf_invalid") from exc
        text = "\n\n".join(page for page in pages if page)
        if len(text.strip()) < 2:
            raise DocumentIngestionError("upload_pdf_no_extractable_text")
        return text
