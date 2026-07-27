import mimetypes
from pathlib import Path

from app.services.collection.base import BaseCollector, CollectionResult


class FileCollector(BaseCollector):
    def collect(self, target: str | Path) -> CollectionResult:
        path = Path(target).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        if path.stat().st_size > self.settings.max_download_bytes:
            raise ValueError(f"File exceeds {self.settings.max_download_bytes} bytes")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return CollectionResult(
            content=path.read_bytes(),
            source_url=path.as_uri(),
            final_url=path.as_uri(),
            content_type=content_type,
            http_status=None,
            title=path.stem,
            metadata={"local_source": True, "original_path": str(path)},
        )
