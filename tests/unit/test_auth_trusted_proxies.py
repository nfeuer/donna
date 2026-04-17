"""Unit tests for client IP resolution with trusted-proxy XFF handling."""

from __future__ import annotations

import ipaddress
from types import SimpleNamespace

from donna.api.auth import trusted_proxies as tp


def _req(client_host: str, xff: str | None) -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff else {}
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        headers=headers,
    )


_CADDY_CIDR = [ipaddress.ip_network("172.18.0.0/16")]


def test_no_xff_uses_client_host():
    ip = tp.client_ip(_req("203.0.113.5", None), trusted_proxies=_CADDY_CIDR)
    assert ip == "203.0.113.5"


def test_xff_from_trusted_proxy_uses_rightmost_entry():
    ip = tp.client_ip(
        _req("172.18.0.2", "1.1.1.1, 203.0.113.9"),
        trusted_proxies=_CADDY_CIDR,
    )
    assert ip == "203.0.113.9"


def test_xff_from_untrusted_source_is_ignored():
    ip = tp.client_ip(
        _req("203.0.113.200", "1.1.1.1"),
        trusted_proxies=_CADDY_CIDR,
    )
    assert ip == "203.0.113.200"


def test_malformed_xff_falls_back_to_client_host():
    ip = tp.client_ip(
        _req("172.18.0.2", "not-an-ip"),
        trusted_proxies=_CADDY_CIDR,
    )
    assert ip == "172.18.0.2"
