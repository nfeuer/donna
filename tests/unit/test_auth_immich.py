"""Unit tests for Immich identity forwarding."""

from __future__ import annotations

import pytest
from aioresponses import aioresponses

from donna.api.auth import immich


@pytest.mark.asyncio
async def test_resolve_returns_user_on_200():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/users/me",
            status=200,
            payload={"id": "ab12", "email": "nick@example.com", "name": "Nick", "isAdmin": True},
        )
        user = await client.resolve(bearer="t0k")
    assert user.immich_user_id == "ab12"
    assert user.email == "nick@example.com"
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_resolve_401_returns_none():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get("http://immich:2283/api/users/me", status=401)
        user = await client.resolve(bearer="bad")
    assert user is None


@pytest.mark.asyncio
async def test_cache_hits_within_ttl():
    client = immich.ImmichClient(internal_url="http://immich:2283", cache_ttl_s=60)
    with aioresponses() as m:
        m.get(
            "http://immich:2283/api/users/me",
            status=200,
            payload={"id": "ab12", "email": "nick@example.com", "name": "Nick", "isAdmin": False},
        )
        first = await client.resolve(bearer="t0k")
        second = await client.resolve(bearer="t0k")
    assert first is not None
    assert second is not None
    assert first.immich_user_id == second.immich_user_id
