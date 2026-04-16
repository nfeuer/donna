"""Client IP resolution from X-Forwarded-For, respecting trusted proxies only.

NEVER read request.client.host or X-Forwarded-For directly outside this
module — always call client_ip(request, trusted_proxies=...).
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
    raw_host = request.client.host if request.client else ""
    try:
        host = ipaddress.ip_address(raw_host)
    except ValueError:
        return raw_host

    if not any(host in cidr for cidr in trusted_proxies):
        return raw_host

    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return raw_host

    parts = [p.strip() for p in xff.split(",") if p.strip()]
    for candidate in reversed(parts):
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            continue
    return raw_host
