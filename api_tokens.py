"""API-token primitives. These helpers do not enable API-key authentication by themselves."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import re

TOKEN_PREFIX = "ak_v1_"
_TOKEN_RE = re.compile(r"^ak_v1_[A-Za-z0-9_-]{43}$")


def generate_api_token() -> str:
    """Generate a 256-bit API token; return it once to the caller for secure delivery."""
    raw = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{TOKEN_PREFIX}{encoded}"


def token_fingerprint(token: str, pepper: str | bytes) -> str:
    """Return a stable HMAC-SHA-256 fingerprint suitable for database storage."""
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise ValueError("Malformed SRX API token")
    key = pepper.encode("utf-8") if isinstance(pepper, str) else pepper
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("API-token pepper must contain at least 32 bytes")
    return hmac.new(key, token.encode("ascii"), hashlib.sha256).hexdigest()


def verify_api_token(token: str, stored_fingerprint: str, pepper: str | bytes) -> bool:
    """Constant-time comparison; malformed tokens or fingerprints fail closed."""
    if not isinstance(stored_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", stored_fingerprint):
        return False
    try:
        candidate = token_fingerprint(token, pepper)
    except (TypeError, ValueError, UnicodeError):
        return False
    return hmac.compare_digest(candidate, stored_fingerprint)
