from pathlib import Path

from app.services.parsing.base import DocumentParser, ParsedDocument, ParsedPage


class TextParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        text = path.read_text(encoding="utf-8-sig")
        return ParsedDocument(path.stem, text, [ParsedPage(1, text)])
