import socket

import httpx
import pytest

from app.core.exceptions import UnsafeUrlError
from app.services.collection import SafeUrlPolicy, WebPageCollector


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/admin",
        "http://172.16.0.1/admin",
        "http://192.168.1.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/admin",
        "http://[fc00::1]/admin",
        "http://[fe80::1]/admin",
    ],
)
def test_private_and_metadata_urls_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        SafeUrlPolicy().resolve_and_validate(url)


def test_url_credentials_are_rejected() -> None:
    with pytest.raises(UnsafeUrlError, match="credentials"):
        SafeUrlPolicy().resolve_and_validate("https://user:password@example.com/")


def test_dns_resolution_to_private_address_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443))],
    )

    with pytest.raises(UnsafeUrlError):
        SafeUrlPolicy().resolve_and_validate("https://public-name.test/")


def test_redirect_to_private_address_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
            request=request,
        )

    collector = WebPageCollector(transport=httpx.MockTransport(handler))

    with pytest.raises(UnsafeUrlError):
        collector.collect("https://public-name.test/start")


def test_registered_source_host_matching() -> None:
    assert SafeUrlPolicy.host_allowed(
        "https://docs.example.com/rule",
        "https://example.com",
        ["partner.test"],
    )
    assert not SafeUrlPolicy.host_allowed(
        "https://example.com.attacker.test/rule",
        "https://example.com",
        [],
    )
