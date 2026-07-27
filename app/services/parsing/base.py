from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedPage:
    page_number: int
    text: str


@dataclass
class ParsedDocument:
    title: str
    plain_text: str
    pages: list[ParsedPage]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument:
        """Parse one local document."""
