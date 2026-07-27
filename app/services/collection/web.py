from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.collection.base import BaseCollector, CollectionResult


class WebPageCollector(BaseCollector):
    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=2),
        reraise=True,
    )
    def collect(self, target: str | Path) -> CollectionResult:
        url = str(target)
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only public HTTP(S) URLs are supported")
        with httpx.Client(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=True,
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.settings.max_download_bytes:
                        raise ValueError("Download exceeds configured size limit")
                content_type = response.headers.get("content-type", "application/octet-stream")
                return CollectionResult(
                    content=bytes(content),
                    source_url=url,
                    final_url=str(response.url),
                    content_type=content_type,
                    http_status=response.status_code,
                    metadata={
                        "response_headers": {"last-modified": response.headers.get("last-modified")}
                    },
                )
