from pathlib import Path

from app.services.collection.base import BaseCollector, CollectionResult
from app.services.collection.file import FileCollector


class LocalDirectoryCollector(BaseCollector):
    supported_suffixes = {".pdf", ".docx", ".html", ".htm", ".txt"}

    def collect(self, target: str | Path) -> CollectionResult:
        raise TypeError("Use collect_all() for directories")

    def collect_all(self, target: str | Path) -> list[CollectionResult]:
        root = Path(target).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        collector = FileCollector(self.settings)
        results: list[CollectionResult] = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in self.supported_suffixes:
                results.append(collector.collect(path))
        return results
