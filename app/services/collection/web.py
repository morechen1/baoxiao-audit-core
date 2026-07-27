from pathlib import Path
from urllib.parse import urljoin

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.exceptions import UnsafeUrlError
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.collection.security import SafeUrlPolicy


class WebPageCollector(BaseCollector):
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        allowed_hosts: frozenset[str],
        allow_subdomains: bool = True,
        transport: httpx.BaseTransport | None = None,
        url_policy: SafeUrlPolicy | None = None,
    ) -> None:
        super().__init__(settings)
        self.transport = transport
        self.url_policy = url_policy or SafeUrlPolicy()
        self.allowed_hosts = allowed_hosts
        self.allow_subdomains = allow_subdomains

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=2),
        reraise=True,
    )
    def collect(self, target: str | Path) -> CollectionResult:
        original_url = str(target)
        self._require_allowed_host(original_url, redirect=False)
        with httpx.Client(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            current_url = original_url
            for _ in range(6):
                self._require_allowed_host(current_url, redirect=current_url != original_url)
                addresses = self.url_policy.resolve_and_validate(current_url)
                with client.stream("GET", current_url) as response:
                    if self.transport is None:
                        stream = response.extensions.get("network_stream")
                        if stream is None:
                            raise RuntimeError("Unable to verify connection peer")
                        peer = stream.get_extra_info("server_addr")
                        if not peer:
                            raise RuntimeError("Unable to read connection peer")
                        self.url_policy.validate_peer(str(peer[0]), addresses)
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise httpx.HTTPError("Redirect response has no Location header")
                        current_url = urljoin(current_url, location)
                        self._require_allowed_host(current_url, redirect=True)
                        continue
                    response.raise_for_status()
                    self._require_allowed_host(
                        str(response.url), redirect=str(response.url) != original_url
                    )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.settings.max_download_bytes:
                            raise ValueError("Download exceeds configured size limit")
                    return CollectionResult(
                        content=bytes(content),
                        source_url=original_url,
                        final_url=str(response.url),
                        content_type=response.headers.get(
                            "content-type", "application/octet-stream"
                        ),
                        http_status=response.status_code,
                        metadata={
                            "response_headers": {
                                "last-modified": response.headers.get("last-modified")
                            },
                            "validated_addresses": sorted(str(value) for value in addresses),
                        },
                    )
            raise httpx.TooManyRedirects(
                "More than five redirects", request=httpx.Request("GET", current_url)
            )

    def _require_allowed_host(self, url: str, *, redirect: bool) -> None:
        if not self.url_policy.host_allowed_for_hosts(
            url,
            self.allowed_hosts,
            allow_subdomains=self.allow_subdomains,
        ):
            code = "redirect_host_not_allowed" if redirect else "source_host_not_allowed"
            raise UnsafeUrlError(code)
