"""In-memory, allowlisted marketing-document ingestion for V2 screening."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import fitz
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.config import Settings, get_settings
from app.core.exceptions import DocumentIngestionError

ALLOWED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf", ".csv"}
ALLOWED_CONTENT_TYPES = {
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".csv": {"text/csv", "application/csv", "text/plain", "application/octet-stream"},
}
TEXT_COLUMNS = ("text", "content", "marketing_text", "文案", "营销文案", "材料")


@dataclass(frozen=True)
class IngestedMaterial:
    title: str
    source_name: str
    source_type: str
    raw_text: str


class MarketingDocumentIngestionService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def ingest(self, filename: str, content: bytes) -> list[IngestedMaterial]:
        safe_name = self._safe_name(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentIngestionError("upload_extension_not_allowed")
        if not content or len(content) > self.settings.max_upload_bytes:
            raise DocumentIngestionError("upload_size_invalid")
        if extension == ".csv":
            return self._csv(safe_name, content)
        if extension in {".txt", ".md"}:
            text = self._decode_text(content)
        elif extension == ".docx":
            text = self._docx(content)
        else:
            text = self._pdf(content)
        return [self._material(safe_name, extension, text)]

    def validate_content_type(self, filename: str, content_type: str | None) -> None:
        safe_name = self._safe_name(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise DocumentIngestionError("upload_extension_not_allowed")
        normalized = (content_type or "application/octet-stream").split(";", 1)[0].lower()
        if normalized not in ALLOWED_CONTENT_TYPES[extension]:
            raise DocumentIngestionError("upload_content_type_not_allowed")

    @staticmethod
    def _safe_name(filename: str) -> str:
        if not filename or "\x00" in filename:
            raise DocumentIngestionError("upload_filename_invalid")
        normalized = filename.replace("\\", "/")
        safe_name = Path(normalized).name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise DocumentIngestionError("upload_filename_invalid")
        return safe_name[:255]

    @staticmethod
    def _decode_text(content: bytes) -> str:
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentIngestionError("upload_text_encoding_invalid") from exc

    @staticmethod
    def _docx(content: bytes) -> str:
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
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                pages = [page.get_text("text").strip() for page in document]
        except Exception as exc:
            raise DocumentIngestionError("upload_pdf_invalid") from exc
        text = "\n\n".join(page for page in pages if page)
        if not text.strip():
            raise DocumentIngestionError("upload_pdf_no_extractable_text")
        return text

    def _csv(self, filename: str, content: bytes) -> list[IngestedMaterial]:
        text = self._decode_text(content)
        try:
            reader = csv.DictReader(io.StringIO(text))
            fieldnames = reader.fieldnames or []
            text_column = next((name for name in TEXT_COLUMNS if name in fieldnames), None)
            if text_column is None and len(fieldnames) == 1:
                text_column = fieldnames[0]
            if text_column is None:
                raise DocumentIngestionError("upload_csv_text_column_missing")
            title_column = next(
                (name for name in ("title", "name", "标题", "名称") if name in fieldnames), None
            )
            materials: list[IngestedMaterial] = []
            for index, row in enumerate(reader, start=1):
                raw_text = (row.get(text_column) or "").strip()
                if not raw_text:
                    continue
                title = (row.get(title_column) or "").strip() if title_column else ""
                materials.append(
                    IngestedMaterial(
                        title=title or f"{Path(filename).stem} 第{index}条",
                        source_name=f"{filename}#{index}",
                        source_type="csv",
                        raw_text=raw_text,
                    )
                )
                if len(materials) > self.settings.max_batch_items:
                    raise DocumentIngestionError("batch_item_limit_exceeded")
        except csv.Error as exc:
            raise DocumentIngestionError("upload_csv_invalid") from exc
        if not materials:
            raise DocumentIngestionError("upload_document_empty")
        return materials

    @staticmethod
    def _material(filename: str, extension: str, text: str) -> IngestedMaterial:
        if not text.strip():
            raise DocumentIngestionError("upload_document_empty")
        return IngestedMaterial(
            title=Path(filename).stem[:300] or "上传材料",
            source_name=filename,
            source_type=extension.removeprefix("."),
            raw_text=text,
        )
