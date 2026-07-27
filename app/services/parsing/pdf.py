from pathlib import Path

import fitz

from app.services.parsing.base import DocumentParser, ParsedDocument, ParsedPage


class PdfParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        document = fitz.open(path)
        pages = [
            ParsedPage(page_number=index + 1, text=page.get_text("text").strip())
            for index, page in enumerate(document)
        ]
        plain_text = "\n\n".join(page.text for page in pages if page.text)
        requires_ocr = bool(pages) and (
            len(plain_text.strip()) < max(20, len(pages) * 10)
            and any(page.get_images(full=True) for page in document)
        )
        metadata = {
            "page_count": len(pages),
            "requires_ocr": requires_ocr,
            "pdf_metadata": dict(document.metadata or {}),
        }
        warnings = ["suspected_scanned_pdf"] if requires_ocr else []
        title = (document.metadata or {}).get("title") or path.stem
        document.close()
        return ParsedDocument(title, plain_text, pages, metadata, warnings)
