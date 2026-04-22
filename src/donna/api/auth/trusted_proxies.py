"""Client IP resolution from X-Forwarded-For, respecting trusted proxies only.

NEVER read request.client.host or X-Forwarded-For directly outside this
module — always call client_ip(request, trusted_proxies=...).

Proxy contract: we return the RIGHTMOST entry in X-Forwarded-For when the
direct peer is a trusted proxy. This assumes the proxy APPENDS the real
client IP to XFF (Caddy's default `reverse_proxy` does this). If a future
proxy PREPENDS instead (some nginx configs), this module will return an
attacker-supplied XFF value — swap `reversed(parts)` for `parts[0]` and
update `config/auth.yaml::trusted_proxies` to match.
"""

from __future__ import annotations

import ipaddress
from typing import Any


def client_ip(
    request: Any,
    *,
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """Resolve the real client IP given the request.

    If request.client.host is in trusted_proxies, parse X-Forwarded-For and
    return the rightmost entry (the client as seen by the trusted proxy).
    Otherwise, return request.client.host unchanged.
    """
    raw_host: str = request.client.host if request.client else ""
    try:
        host = ipaddress.ip_address(raw_host)
    except ValueError:
        return raw_host

    if not any(host in cidr for cidr in trusted_proxies):
        return raw_host

    xff: str = request.headers.get("x-forwarded-for", "")
    if not xff:
        return raw_host

    parts: list[str] = [p.strip() for p in xff.split(",") if p.strip()]
    for candidate in reversed(parts):
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            continue
    return raw_host
