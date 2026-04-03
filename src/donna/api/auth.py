"""Firebase JWT authentication for the Donna REST API.

Validates Bearer tokens issued by Firebase Auth, then maps the Firebase UID
to a Donna user_id for all downstream DB operations.

No Firebase Admin SDK is needed — validation is done offline using Firebase's
public JWKS endpoint, as described in docs/architecture.md (Auth Flow).

Environment variables:
    FIREBASE_PROJECT_ID   — Firebase project ID (used as JWT audience).
    DONNA_USER_MAP        — Comma-separated "firebase_uid:donna_user_id" pairs.
                            Example: "abc123:nick,def456:dad"
    DONNA_DEFAULT_USER_ID — Fallback user_id when no map entry matches (default: "nick").
    DONNA_AUTH_DISABLED   — Set to "true" to bypass auth in dev mode.
"""

from __future__ import annotations

import os
import time
from typing import Annotated

import aiohttp
import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = structlog.get_logger()

# Firebase JWKS endpoint — public keys rotate every few hours.
_GOOGLE_JWKS_URL = (
    "https://www.googleapis.com/service_accounts/v1/jwk/"
    "securetoken@system.gserviceaccount.com"
)

_FIREBASE_PROJECT_ID: str = os.environ.get("FIREBASE_PROJECT_ID", "")

# In-process JWKS cache (keys rotate roughly every 6 hours; refresh hourly).
_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL_SECONDS: float = 3600.0

_bearer = HTTPBearer(auto_error=False)


async def _get_jwks() -> dict:
    """Fetch Firebase public keys, using a 1-hour in-process cache."""
    global _jwks_cache, _jwks_fetched_at

    now = time.monotonic()
    if _jwks_cache and now - _jwks_fetched_at < _JWKS_TTL_SECONDS:
        return _jwks_cache

    async with aiohttp.ClientSession() as session:
        async with session.get(_GOOGLE_JWKS_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data: dict = await resp.json(content_type=None)

    _jwks_cache = data
    _jwks_fetched_at = now
    logger.info("firebase_jwks_refreshed", key_count=len(data.get("keys", [])))
    return _jwks_cache


def _parse_user_map() -> dict[str, str]:
    """Parse DONNA_USER_MAP env var into a firebase_uid → donna_user_id dict."""
    raw = os.environ.get("DONNA_USER_MAP", "")
    result: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            firebase_uid, donna_id = entry.split(":", 1)
            result[firebase_uid.strip()] = donna_id.strip()
    return result


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Validate a Firebase JWT Bearer token and return the Donna user_id.

    In dev mode (DONNA_AUTH_DISABLED=true), returns DONNA_DEFAULT_USER_ID
    immediately without any token validation.

    Raises HTTP 401 on missing or invalid tokens.
    """
    default_user_id = os.environ.get("DONNA_DEFAULT_USER_ID", "nick")

    # Dev mode bypass — never enable in production.
    if os.environ.get("DONNA_AUTH_DISABLED", "").lower() == "true":
        logger.debug("auth_disabled_dev_mode", user_id=default_user_id)
        return default_user_id

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid", "")

        jwks = await _get_jwks()
        public_key = None
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)  # type: ignore[attr-defined]
                break

        if public_key is None:
            logger.warning("firebase_jwt_unknown_kid", kid=kid)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token signing key not recognised",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload: dict = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            audience=_FIREBASE_PROJECT_ID or None,
            options={"verify_exp": True, "verify_aud": bool(_FIREBASE_PROJECT_ID)},
        )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("firebase_jwt_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except aiohttp.ClientError as exc:
        logger.error("firebase_jwks_fetch_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service temporarily unavailable",
        )

    # uid claim is preferred; fall back to sub.
    firebase_uid: str = payload.get("uid") or payload.get("sub", "")

    user_map = _parse_user_map()
    user_id = user_map.get(firebase_uid, default_user_id)

    logger.debug("firebase_jwt_validated", firebase_uid=firebase_uid, donna_user_id=user_id)
    return user_id


# Convenience type alias for route signatures.
CurrentUser = Annotated[str, Depends(get_current_user_id)]
