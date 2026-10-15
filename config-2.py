from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    database_path: str = os.getenv("SRX_DATABASE_PATH", "./data/srx.sqlite3")
    oidc_issuer: str = os.getenv("SRX_OIDC_ISSUER", "")
    oidc_audience: str = os.getenv("SRX_OIDC_AUDIENCE", "")
    oidc_jwks_url: str = os.getenv("SRX_OIDC_JWKS_URL", "")
    api_token_pepper: str = os.getenv("SRX_API_TOKEN_PEPPER", "")
    environment: str = os.getenv("SRX_ENV", "development")
    max_page_size: int = 100
    max_bundle_bytes: int = 1_000_000

settings = Settings()

def ensure_database_parent(path: str) -> None:
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
