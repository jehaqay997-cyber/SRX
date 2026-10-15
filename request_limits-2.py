from __future__ import annotations

import json
from collections import deque
from typing import Any, Awaitable, Callable

ASGIApp = Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]]


class RequestBodyLimitMiddleware:
    """Apply a bounded body size to write requests before JSON parsing occurs.

    This limits application-level buffering to max_bytes plus at most one ASGI
    receive chunk. The ASGI server/proxy should also enforce a request-size limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 1_000_000) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        if scope.get("type") != "http" or scope.get("method", "GET").upper() not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                length = int(declared.decode("ascii"))
            except (ValueError, UnicodeDecodeError):
                await self._respond(send, 400, "INVALID_CONTENT_LENGTH", "Content-Length must be a non-negative integer.")
                return
            if length < 0:
                await self._respond(send, 400, "INVALID_CONTENT_LENGTH", "Content-Length must be a non-negative integer.")
                return
            if length > self.max_bytes:
                await self._respond(send, 413, "REQUEST_TOO_LARGE", "Request body exceeds configured size limit.")
                return

        buffered: deque[dict[str, Any]] = deque()
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                buffered.append(message)
                break
            if message["type"] != "http.request":
                buffered.append(message)
                break
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await self._respond(send, 413, "REQUEST_TOO_LARGE", "Request body exceeds configured size limit.")
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict[str, Any]:
            if buffered:
                return buffered.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _respond(send: Callable[[dict[str, Any]], Awaitable[None]], status_code: int, code: str, message: str) -> None:
        body = json.dumps({"detail": {"code": code, "message": message}}, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status_code, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})
