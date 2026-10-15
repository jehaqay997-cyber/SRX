from srx_api import main


def test_security_headers_present_on_health(client):
    response = client.get('/v1/health')
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'camera=()' in response.headers['permissions-policy']


def test_api_token_responses_are_not_cacheable(client, auth_headers, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(main, 'settings', SimpleNamespace(api_token_pepper='p' * 32))
    response = client.post('/v1/api-tokens', headers=auth_headers,
                           json={'label': 'test key', 'expires_in_days': 30})
    assert response.status_code == 201
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['pragma'] == 'no-cache'


def test_hsts_only_in_production(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from srx_api.security_headers import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, production=True)

    @app.get('/')
    def root():
        return {'ok': True}

    response = TestClient(app).get('/')
    assert response.headers['strict-transport-security'] == 'max-age=31536000'
