from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.exceptions import UnsafeUrlError


class SafeUrlPolicy:
    def resolve_and_validate(
        self, url: str
    ) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeUrlError("Only HTTP and HTTPS URLs are allowed")
        if parsed.username or parsed.password:
            raise UnsafeUrlError("URL credentials are forbidden")
        hostname = parsed.hostname
        if not hostname:
            raise UnsafeUrlError("URL hostname is required")
        normalized = hostname.rstrip(".").lower()
        if normalized == "localhost" or normalized.endswith(".localhost"):
            raise UnsafeUrlError("localhost is forbidden")
        try:
            addresses = {ipaddress.ip_address(normalized)}
        except ValueError:
            try:
                addresses = {
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(
                        normalized,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                }
            except socket.gaierror as exc:
                raise UnsafeUrlError(f"DNS resolution failed for {normalized}") from exc
        if not addresses:
            raise UnsafeUrlError("DNS resolution returned no addresses")
        for address in addresses:
            if (
                address.is_loopback
                or address.is_private
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
                or not address.is_global
            ):
                raise UnsafeUrlError("URL resolves to a prohibited network address")
        return frozenset(addresses)

    @staticmethod
    def validate_peer(
        peer_host: str,
        validated_addresses: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        try:
            peer = ipaddress.ip_address(peer_host)
        except ValueError as exc:
            raise UnsafeUrlError("Connection peer address is invalid") from exc
        if peer not in validated_addresses:
            raise UnsafeUrlError("Connection peer differs from validated DNS result")

    @staticmethod
    def host_allowed(url: str, base_url: str | None, allowed_domains: list[str]) -> bool:
        target = (urlparse(url).hostname or "").rstrip(".").lower()
        base_host = (urlparse(base_url or "").hostname or "").rstrip(".").lower()
        allowed = {base_host, *(value.rstrip(".").lower() for value in allowed_domains)}
        allowed.discard("")
        return any(target == host or target.endswith(f".{host}") for host in allowed)
