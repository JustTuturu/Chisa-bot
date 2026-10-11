"""Shared URL validation (SSRF guard)."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable

# Networks blocked for outbound fetches (SSRF): loopback, private, link-local, CGNAT.
BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
]


def default_resolver(hostname: str) -> list[str | int]:
    infos = socket.getaddrinfo(hostname, None)
    return [info[4][0] for info in infos]


def validate_url(url: str, resolver: Callable[[str], list[str | int]] = default_resolver) -> str:
    """Validate a URL for outbound fetching."""
    raw = str(url).strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ValueError(f"Malformed URL {raw!r}.") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs are allowed, got scheme {parsed.scheme!r}.")
    if not parsed.hostname:
        raise ValueError(f"URL {raw!r} has no hostname.")
    try:
        ips = resolver(parsed.hostname)
    except OSError as exc:
        raise ValueError(f"Could not resolve host {parsed.hostname!r}: {exc}") from exc
    if not ips:
        raise ValueError(f"Host {parsed.hostname!r} does not resolve.")
    for ip_raw in ips:
        ip = ipaddress.ip_address(ip_raw)
        if any(ip in net for net in BLOCKED_NETWORKS):
            raise ValueError(
                f"Refusing to fetch from {parsed.hostname!r}: it resolves to a "
                "private/internal address (SSRF protection)."
            )
    return raw
