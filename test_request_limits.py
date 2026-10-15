from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from srx_api import main


class StreamingRequest:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = headers or {}
        self.consumed = 0

    async def stream(self):
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


@pytest.mark.anyio
async def test_limited_reader_stops_at_first_oversized_chunk():
    request = StreamingRequest([b"123456789", b"should-not-be-read"])
    with pytest.raises(HTTPException) as exc:
        await main.read_limited_body(request, max_bytes=8)
    assert exc.value.status_code == 413
    assert request.consumed == 1


@pytest.mark.anyio
async def test_limited_reader_rejects_declared_oversize_before_streaming():
    request = StreamingRequest([b"should-not-be-read"], headers={"content-length": "100"})
    with pytest.raises(HTTPException) as exc:
        await main.read_limited_body(request, max_bytes=10)
    assert exc.value.status_code == 413
    assert request.consumed == 0


@pytest.mark.anyio
async def test_limited_reader_rejects_malformed_content_length():
    request = StreamingRequest([], headers={"content-length": "not-a-number"})
    with pytest.raises(HTTPException) as exc:
        await main.read_limited_body(request, max_bytes=10)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_CONTENT_LENGTH"


def test_validate_bundle_returns_413_for_oversized_body(client, auth_headers, monkeypatch):
    monkeypatch.setattr(main, "settings", SimpleNamespace(max_bundle_bytes=8))
    response = client.post(
        "/v1/validate-bundle",
        content=b'{"sources": []}',
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "BUNDLE_TOO_LARGE"


def test_validate_bundle_accepts_body_at_exact_limit(client, auth_headers, monkeypatch):
    body = b'{"sources":[]}'
    monkeypatch.setattr(main, "settings", SimpleNamespace(max_bundle_bytes=len(body)))
    response = client.post(
        "/v1/validate-bundle",
        content=body,
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["persisted"] is False


def test_database_readiness_detects_missing_required_table(tmp_path):
    database = main.Database(str(tmp_path / "partial.sqlite3"))
    with database.connection() as conn:
        conn.execute("DROP TABLE audit_events")
    assert database.readiness() is False


def test_database_readiness_passes_for_complete_schema(tmp_path):
    database = main.Database(str(tmp_path / "complete.sqlite3"))
    assert database.readiness() is True


def test_general_write_route_rejects_oversized_declared_body(client, auth_headers):
    response = client.post("/v1/sources", content=b"x" * 1_000_001, headers={**auth_headers, "Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


def test_general_write_route_rejects_oversized_body_without_content_length():
    import asyncio
    from srx_api.request_limits import RequestBodyLimitMiddleware

    events = [
        {"type": "http.request", "body": b"12345", "more_body": True},
        {"type": "http.request", "body": b"67890", "more_body": False},
    ]
    sent = []
    async def receive():
        return events.pop(0)
    async def send(message):
        sent.append(message)
    async def app(scope, receive, send):
        raise AssertionError("downstream app must not run for oversized body")
    middleware = RequestBodyLimitMiddleware(app, max_bytes=8)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 413
    assert b"REQUEST_TOO_LARGE" in sent[1]["body"]
