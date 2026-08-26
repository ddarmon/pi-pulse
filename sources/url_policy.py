"""Syntactic public-URL policy shared by deterministic pipeline gates."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

MAX_URL_CHARS = 2048
MAX_URL_QUERY_CHARS = 512


class UrlPolicyError(ValueError):
    """Raised when a URL is outside the outbound public-web policy."""


def validate_public_url(raw: str, *, resolve: bool = False) -> str:
    """Validate an http(s) URL and optionally reject private DNS answers.

    The runtime JavaScript broker always performs the resolving form before
    every redirect hop. Python uses the non-resolving form while filtering a
    model-authored signal sheet, keeping that stage deterministic/offline.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UrlPolicyError("missing-url")
    value = raw.strip()
    if len(value) > MAX_URL_CHARS or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        raise UrlPolicyError("whitespace-or-length")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UrlPolicyError("malformed-url") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UrlPolicyError("bad-scheme")
    if parsed.username is not None or parsed.password is not None:
        raise UrlPolicyError("embedded-credentials")
    if len(parsed.query) > MAX_URL_QUERY_CHARS:
        raise UrlPolicyError("query-too-long")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname or any(char in hostname for char in "<>"):
        raise UrlPolicyError("malformed-host")
    if "." not in hostname:
        # A doubled scheme parses as a legal single-label host: a live signal
        # sheet emitted `https://https://arxiv.org/html/2512.16959v1`, whose
        # host is `https`. It passed every gate, DNS-failed at fetch time, and
        # the slot silently degraded to a search snippet. No public web host
        # is single-label, so reject the shape where it is still cheap.
        raise UrlPolicyError("dotless-host")
    expected = 443 if parsed.scheme.lower() == "https" else 80
    if port is not None:
        if port != expected:
            raise UrlPolicyError("non-standard-port")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise UrlPolicyError("ip-literal")
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".home.arpa")
    ):
        raise UrlPolicyError("local-hostname")
    try:
        hostname.encode("idna")
    except UnicodeError as exc:
        raise UrlPolicyError("malformed-host") from exc

    if resolve:
        try:
            answers = socket.getaddrinfo(hostname, port or expected, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UrlPolicyError("dns-failed") from exc
        addresses = {answer[4][0].split("%")[0] for answer in answers}
        if not addresses:
            raise UrlPolicyError("dns-empty")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UrlPolicyError("non-public-address")
    return value
