from __future__ import annotations

from pathlib import Path

from app.services.collection.nfra import parse_nfra_public_payload, require_nfra_document_quality
from app.services.parsing.base import DocumentParser, ParsedDocument, ParsedPage


class NfraJsonParser(DocumentParser):
    def parse(self, path: Path) -> ParsedDocument:
        payload = parse_nfra_public_payload(path.read_bytes())
        require_nfra_document_quality(payload, None)
        parts = [payload.title]
        if payload.document_number:
            parts.append(payload.document_number)
        parts.append(payload.plain_text)
        plain_text = "\n".join(parts)
        return ParsedDocument(
            title=payload.title,
            plain_text=plain_text,
            pages=[ParsedPage(1, plain_text)],
            metadata={
                "source_format": "nfra_public_json",
                "publisher": payload.publisher,
                "published_at": (
                    payload.published_at.isoformat() if payload.published_at else None
                ),
                "document_number": payload.document_number,
                "nfra": {
                    "doc_id": payload.doc_id,
                    "doc_title": payload.title,
                    "document_number": payload.document_number,
                    "caption": payload.caption_document_number,
                    "publish_date": (
                        payload.published_at.isoformat() if payload.published_at else None
                    ),
                },
            },
        )
