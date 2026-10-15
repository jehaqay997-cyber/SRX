import os
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from srx_api import main, security

ISSUER = "https://identity.example.test/"
AUDIENCE = "srx-api-test"


class FakeSigningKey:
    def __init__(self, key):
        self.key = key


class FakeJwksClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        header = jwt.get_unverified_header(token)
        if header.get("kid") != "test-key":
            raise jwt.InvalidKeyError("unknown kid")
        return FakeSigningKey(self.public_key)


@pytest.fixture()
def signing_material(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(oidc_issuer=ISSUER, oidc_audience=AUDIENCE,
                       oidc_jwks_url="https://identity.example.test/.well-known/jwks.json"),
    )
    monkeypatch.setattr(security, "_jwks_client", lambda url: FakeJwksClient(public_key))
    return private_key


def make_token(private_key, scopes=("srx:read", "srx:write"), subject="user-123", **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": " ".join(scopes),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture()
def client(monkeypatch, signing_material):
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    main.db = main.Database(path)
    with TestClient(main.app) as c:
        yield c
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@pytest.fixture()
def auth_headers(signing_material):
    return {"Authorization": f"Bearer {make_token(signing_material)}"}


@pytest.fixture()
def token_factory(signing_material):
    return lambda **kwargs: make_token(signing_material, **kwargs)
