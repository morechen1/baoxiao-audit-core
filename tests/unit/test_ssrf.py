import socket

import httpx
import pytest

from app.core.exceptions import UnsafeUrlError
from app.services.collection import SafeUrlPolicy, WebPageCollector


@pytest.fixture
def public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )


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

    collector = WebPageCollector(
        allowed_hosts=frozenset({"public-name.test", "169.254.169.254"}),
        transport=httpx.MockTransport(handler),
    )

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


def test_same_domain_redirect_is_allowed(public_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(200, content=b"ok", request=request)

    collector = WebPageCollector(
        allowed_hosts=frozenset({"source.test"}),
        transport=httpx.MockTransport(handler),
    )

    result = collector.collect("https://source.test/start")
    assert result.final_url == "https://source.test/final"


def test_legal_subdomain_redirect_is_allowed(public_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "source.test":
            return httpx.Response(
                302,
                headers={"location": "https://docs.source.test/final"},
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    collector = WebPageCollector(
        allowed_hosts=frozenset({"source.test"}),
        transport=httpx.MockTransport(handler),
    )
    assert collector.collect("https://source.test/start").final_url == (
        "https://docs.source.test/final"
    )


def test_explicit_allowed_domain_redirect_is_allowed(public_dns) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "source.test":
            return httpx.Response(
                302,
                headers={"location": "https://partner.test/final"},
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    collector = WebPageCollector(
        allowed_hosts=frozenset({"source.test", "partner.test"}),
        transport=httpx.MockTransport(handler),
    )
    assert collector.collect("https://source.test/start").final_url == (
        "https://partner.test/final"
    )


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://attacker.test/file",
        "https://source.test.attacker.test/file",
    ],
)
def test_unauthorized_public_redirect_is_rejected(public_dns, redirect_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": redirect_url}, request=request)

    collector = WebPageCollector(
        allowed_hosts=frozenset({"source.test"}),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(UnsafeUrlError, match="redirect_host_not_allowed"):
        collector.collect("https://source.test/start")
