"""Unit tests for AuthConfig loader."""

from __future__ import annotations

import ipaddress

import pytest
import yaml

from donna.api.auth import config as auth_config


def test_load_parses_yaml_and_casts_cidrs(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "ip_gate": {
                    "default_trust_duration": "30d",
                    "durations_allowed": ["24h", "7d", "30d", "90d"],
                    "rate_limit_per_ip": {
                        "request_access": {"max": 5, "window_seconds": 3600},
                        "verify": {"max": 10, "window_seconds": 600},
                    },
                },
                "trusted_proxies": ["172.18.0.0/16"],
                "internal_cidrs": ["172.18.0.0/16"],
                "immich": {
                    "internal_url": "http://immich:2283",
                    "external_url": "https://immich.houseoffeuer.com",
                    "admin_api_key_env": "IMMICH_ADMIN_API_KEY",
                    "user_cache_ttl_seconds": 60,
                    "allowlist_sync_interval_seconds": 900,
                    "allowlist_stale_tolerance_seconds": 86400,
                },
                "device_tokens": {
                    "sliding_window_days": 90,
                    "absolute_max_days": 365,
                    "max_per_user": 10,
                },
                "email": {
                    "from": "donna@houseoffeuer.com",
                    "subject": "Donna access verification",
                    "verify_base_url": "https://donna.houseoffeuer.com/auth/verify",
                    "token_expiry_minutes": 15,
                },
                "bootstrap": {"admin_email_env": "DONNA_BOOTSTRAP_ADMIN_EMAIL"},
            }
        )
    )
    cfg = auth_config.load(p)
    assert cfg.device_tokens.sliding_window_days == 90
    assert cfg.trusted_proxies == [ipaddress.ip_network("172.18.0.0/16")]
    assert cfg.internal_cidrs == [ipaddress.ip_network("172.18.0.0/16")]


def test_load_rejects_empty_trusted_proxies(tmp_path):
    p = tmp_path / "auth.yaml"
    p.write_text(yaml.safe_dump({"trusted_proxies": []}))
    with pytest.raises(ValueError, match="trusted_proxies"):
        auth_config.load(p)
