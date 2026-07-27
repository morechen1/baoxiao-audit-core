from abc import ABC, abstractmethod
from pathlib import Path


class OCRProvider(ABC):
    @abstractmethod
    def extract_text(self, path: Path) -> str:
        """Extract text or fail explicitly when disabled."""
