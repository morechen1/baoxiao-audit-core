from pathlib import Path

from app.providers.ocr.base import OCRProvider


class DisabledOCRProvider(OCRProvider):
    def extract_text(self, path: Path) -> str:
        raise RuntimeError(f"OCR is disabled; no text extracted from {path.name}")
