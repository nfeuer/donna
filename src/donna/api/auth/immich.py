"""Immich identity provider: forward cookie/bearer to Immich /api/users/me."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

import aiohttp
import structlog

logger = structlog.get_logger()

_DEFAULT_CACHE_MAX_ENTRIES = 1024


@dataclass(frozen=True)
class ImmichUser:
    immich_user_id: str
    email: str
    name: str | None
    is_admin: bool


class ImmichClient:
    def __init__(
        self,
        *,
        internal_url: str,
        cache_ttl_s: int = 60,
        cache_max_entries: int = _DEFAULT_CACHE_MAX_ENTRIES,
    ) -> None:
        self._url = internal_url.rstrip("/")
        self._ttl = cache_ttl_s
        self._max = cache_max_entries
        self._cache: OrderedDict[str, tuple[float, ImmichUser | None]] = OrderedDict()

    def _cache_set(self, key: str, value: tuple[float, ImmichUser | None]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    async def resolve(self, *, bearer: str) -> ImmichUser | None:
        key = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self._ttl:
            self._cache.move_to_end(key)
            return cached[1]

        headers = {"Authorization": f"Bearer {bearer}"}
        try:
            async with aiohttp.ClientSession() as session, session.get(
                f"{self._url}/api/users/me",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 401:
                    self._cache_set(key, (now, None))
                    return None
                resp.raise_for_status()
                data = await resp.json()
        except aiohttp.ClientError as exc:
            logger.warning("immich_resolve_failed", error=str(exc))
            return None

        user = ImmichUser(
            immich_user_id=data["id"],
            email=data["email"],
            name=data.get("name"),
            is_admin=bool(data.get("isAdmin", False)),
        )
        self._cache_set(key, (now, user))
        return user
