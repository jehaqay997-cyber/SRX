from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from jwt import PyJWKClient, PyJWTError
from jwt.exceptions import PyJWKClientError
from urllib.error import URLError

from .config import settings


@dataclass(frozen=True)
class Principal:
    """Identity derived only from a cryptographically verified OIDC access token."""

    subject: str
    issuer: str
    scopes: frozenset[str]


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # PyJWKClient caches the JWKS response according to its own cache settings.
    return PyJWKClient(jwks_url, timeout=3, lifespan=300)


def _auth_error(code: str, message: str, status_code: int = 401) -> HTTPException:
    headers = {"WWW-Authenticate": 'Bearer realm="srx"'} if status_code == 401 else None
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def _extract_scopes(claims: dict[str, Any]) -> frozenset[str]:
    raw_scope = claims.get("scope", "")
    scopes = set(raw_scope.split()) if isinstance(raw_scope, str) else set()
    raw_scp = claims.get("scp", [])
    if isinstance(raw_scp, list) and all(isinstance(item, str) for item in raw_scp):
        scopes.update(raw_scp)
    return frozenset(scopes)


async def get_current_principal(
    authorization: str | None = Header(default=None),
) -> Principal:
    """Validate issuer, audience, signature, expiry, and subject; fail closed if misconfigured."""
    issuer = settings.oidc_issuer
    audience = settings.oidc_audience
    jwks_url = settings.oidc_jwks_url
    if not issuer or not audience or not jwks_url:
        raise _auth_error(
            "AUTH_NOT_CONFIGURED",
            "OIDC issuer, audience, and JWKS URL must be configured before protected routes can be used.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not authorization:
        raise _auth_error("AUTH_REQUIRED", "A valid OIDC bearer token is required.")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise _auth_error("AUTH_REQUIRED", "A valid OIDC bearer token is required.")

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token.strip())
        claims = jwt.decode(
            token.strip(),
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            leeway=30,
        )
    except (PyJWTError, PyJWKClientError, URLError, OSError, TimeoutError, ValueError) as exc:
        # Do not expose JWT/JWKS parsing detail to clients. Catching network/JWKS errors is
        # intentional because authentication must fail closed when the identity provider fails.
        raise _auth_error("AUTH_INVALID", "The bearer token is invalid or could not be verified.") from None

    subject = claims.get("sub")
    token_issuer = claims.get("iss")
    if not isinstance(subject, str) or not subject.strip() or token_issuer != issuer:
        raise _auth_error("AUTH_INVALID", "The bearer token does not contain a valid identity.")

    return Principal(subject=subject, issuer=token_issuer, scopes=_extract_scopes(claims))


def require_scopes(*required_scopes: str) -> Callable[..., Any]:
    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise _auth_error(
                "INSUFFICIENT_SCOPE",
                "The authenticated identity lacks the required permission.",
                status.HTTP_403_FORBIDDEN,
            )
        return principal

    return dependency


require_read_scope = require_scopes("srx:read")
require_write_scope = require_scopes("srx:write")
