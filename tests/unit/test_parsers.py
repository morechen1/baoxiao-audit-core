from pathlib import Path

import fitz
from docx import Document

from app.services.parsing import DocxParser, HtmlParser, PdfParser


def test_pdf_parser_preserves_page_numbers(tmp_path: Path) -> None:
    path = tmp_path / "sample.pdf"
    pdf = fitz.open()
    for text in ("第一页测试内容", "第二页测试内容"):
        page = pdf.new_page()
        page.insert_text((72, 72), text, fontname="china-s")
    pdf.save(path)
    pdf.close()

    result = PdfParser().parse(path)

    assert [page.page_number for page in result.pages] == [1, 2]
    assert len(result.pages) == 2
    assert result.metadata["requires_ocr"] is False


def test_docx_parser_preserves_headings(tmp_path: Path) -> None:
    path = tmp_path / "sample.docx"
    document = Document()
    document.add_heading("演示产品条款", level=0)
    document.add_heading("责任范围", level=1)
    document.add_paragraph("仅用于测试。")
    document.save(path)

    result = DocxParser().parse(path)

    assert result.title == "演示产品条款"
    assert "责任范围" in result.plain_text
    assert result.metadata["headings"]


def test_html_parser_removes_navigation_and_scripts(sample_html: Path) -> None:
    result = HtmlParser().parse(sample_html)

    assert result.title == "演示规则"
    assert "仅用于测试" in result.plain_text
    assert "无关导航" not in result.plain_text
    assert "danger" not in result.plain_text
