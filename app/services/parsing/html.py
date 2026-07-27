from pathlib import Path

from bs4 import BeautifulSoup

from app.services.parsing.base import DocumentParser, ParsedDocument, ParsedPage


class HtmlParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else path.stem
        root = soup.find("main") or soup.find("article") or soup.body or soup
        text = "\n".join(
            line.strip() for line in root.get_text("\n", strip=True).splitlines() if line.strip()
        )
        return ParsedDocument(title=title, plain_text=text, pages=[ParsedPage(1, text)])
