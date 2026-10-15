from types import SimpleNamespace
from srx_api import main


def test_token_lifecycle_create_list_revoke_once(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(api_token_pepper="p" * 32))
    created = client.post("/v1/api-tokens", json={"label":"automation", "expires_in_days":30}, headers=auth_headers)
    assert created.status_code == 201
    body = created.json()
    assert body["token"].startswith("ak_v1_")
    assert body["expires_at"] and body["id"]
    token_id = body["id"]

    listed = client.get("/v1/api-tokens", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "active"
    assert "token" not in listed.json()["items"][0]

    revoked = client.post(f"/v1/api-tokens/{token_id}/revoke", headers=auth_headers)
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    listed_again = client.get("/v1/api-tokens", headers=auth_headers).json()
    assert listed_again["items"][0]["status"] == "revoked"

    with main.db.connection() as conn:
        row = conn.execute("SELECT fingerprint FROM api_tokens WHERE id=?", (token_id,)).fetchone()
        assert row is not None
        assert body["token"] not in row["fingerprint"]
        assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE resource_type='api_token'").fetchone()[0] == 2


def test_token_management_requires_auth(client):
    assert client.get("/v1/api-tokens").status_code == 401
    assert client.post("/v1/api-tokens", json={"label":"x"}).status_code == 401


def test_token_create_fails_closed_without_pepper(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(api_token_pepper="short"))
    response = client.post("/v1/api-tokens", json={"label":"automation"}, headers=auth_headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "TOKEN_SERVICE_NOT_CONFIGURED"


def test_token_owner_cannot_revoke_another_owners_token(client, auth_headers, token_factory, signing_material, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(api_token_pepper="p" * 32))
    created = client.post("/v1/api-tokens", json={"label":"owned"}, headers=auth_headers)
    token_id = created.json()["id"]
    other_headers = {"Authorization": f"Bearer {token_factory(subject='different-user')}"}
    response = client.post(f"/v1/api-tokens/{token_id}/revoke", headers=other_headers)
    assert response.status_code == 404


def test_token_create_rejects_unknown_fields_and_invalid_lifetime(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(api_token_pepper="p" * 32))
    unknown = client.post("/v1/api-tokens", json={"label":"x", "owner_subject":"admin"}, headers=auth_headers)
    assert unknown.status_code == 422
    invalid = client.post("/v1/api-tokens", json={"label":"x", "expires_in_days":0}, headers=auth_headers)
    assert invalid.status_code == 422
