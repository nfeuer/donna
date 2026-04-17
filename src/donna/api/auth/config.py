"""Auth config loader with strict validation. Fail-closed on missing keys."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RateLimit:
    max: int
    window_seconds: int


@dataclass(frozen=True)
class IPGateConfig:
    default_trust_duration: str
    durations_allowed: list[str]
    rate_limit_request_access: RateLimit
    rate_limit_verify: RateLimit


@dataclass(frozen=True)
class ImmichSettings:
    internal_url: str
    external_url: str
    admin_api_key_env: str
    user_cache_ttl_seconds: int
    allowlist_sync_interval_seconds: int
    allowlist_stale_tolerance_seconds: int


@dataclass(frozen=True)
class DeviceTokenSettings:
    sliding_window_days: int
    absolute_max_days: int
    max_per_user: int


@dataclass(frozen=True)
class EmailSettings:
    from_addr: str
    subject: str
    verify_base_url: str
    token_expiry_minutes: int


@dataclass(frozen=True)
class BootstrapSettings:
    admin_email_env: str


@dataclass(frozen=True)
class AuthConfig:
    ip_gate: IPGateConfig
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
    internal_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
    immich: ImmichSettings
    device_tokens: DeviceTokenSettings
    email: EmailSettings
    bootstrap: BootstrapSettings


def _parse_cidrs(raw: list) -> list:
    if not raw:
        raise ValueError("trusted_proxies/internal_cidrs must be non-empty")
    return [ipaddress.ip_network(c) for c in raw]


def load(path: Path) -> AuthConfig:
    data = yaml.safe_load(path.read_text())
    if not data:
        raise ValueError("auth.yaml is empty")

    trusted = _parse_cidrs(data.get("trusted_proxies") or [])
    internal = _parse_cidrs(data.get("internal_cidrs") or [])

    ig = data["ip_gate"]
    rate = ig["rate_limit_per_ip"]
    ip_gate = IPGateConfig(
        default_trust_duration=ig["default_trust_duration"],
        durations_allowed=list(ig["durations_allowed"]),
        rate_limit_request_access=RateLimit(**rate["request_access"]),
        rate_limit_verify=RateLimit(**rate["verify"]),
    )

    im = data["immich"]
    immich = ImmichSettings(
        internal_url=im["internal_url"],
        external_url=im["external_url"],
        admin_api_key_env=im["admin_api_key_env"],
        user_cache_ttl_seconds=int(im["user_cache_ttl_seconds"]),
        allowlist_sync_interval_seconds=int(im["allowlist_sync_interval_seconds"]),
        allowlist_stale_tolerance_seconds=int(im["allowlist_stale_tolerance_seconds"]),
    )

    dt = data["device_tokens"]
    device_tokens = DeviceTokenSettings(
        sliding_window_days=int(dt["sliding_window_days"]),
        absolute_max_days=int(dt["absolute_max_days"]),
        max_per_user=int(dt["max_per_user"]),
    )

    em = data["email"]
    email = EmailSettings(
        from_addr=em["from"],
        subject=em["subject"],
        verify_base_url=em["verify_base_url"],
        token_expiry_minutes=int(em["token_expiry_minutes"]),
    )

    bs = data["bootstrap"]
    bootstrap = BootstrapSettings(admin_email_env=bs["admin_email_env"])

    return AuthConfig(
        ip_gate=ip_gate,
        trusted_proxies=trusted,
        internal_cidrs=internal,
        immich=immich,
        device_tokens=device_tokens,
        email=email,
        bootstrap=bootstrap,
    )
