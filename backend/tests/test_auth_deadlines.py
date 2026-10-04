"""Auth provider streams must fit one elapsed request budget."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

import personal_travel.api.middleware as auth_middleware
import personal_travel.api.routes.auth as auth_routes
from personal_travel.auth.google_oidc import IdentityProviderUnavailable
from personal_travel.config import Settings


def test_oauth_code_exchange_rejects_trickling_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.02)
                yield b" "

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == auth_routes.GOOGLE_TOKEN_URL
        return httpx.Response(200, stream=SlowStream(), request=request)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle)

    def client(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(auth_routes.httpx, "AsyncClient", client)
    monkeypatch.setattr(auth_routes, "OAUTH_EXCHANGE_DEADLINE_SECONDS", 0.05)
    settings = Settings(
        travel_auth_mode="google_oidc",
        google_oauth_client_id="travel-client.apps.googleusercontent.com",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
    )
    with pytest.raises(IdentityProviderUnavailable):
        asyncio.run(auth_routes._exchange_code("synthetic-code", "synthetic-verifier", settings))


def test_session_lookup_does_not_block_other_asgi_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        travel_auth_mode="google_oidc",
        google_oauth_client_id="travel-client.apps.googleusercontent.com",
        google_oauth_client_secret="synthetic-secret",
        google_oauth_redirect_uri="https://travel.test/auth/google/callback",
        google_oauth_allowed_email="owner@gmail.com",
    )

    class DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def slow_lookup(*_args: object) -> None:
        time.sleep(0.15)
        return None

    monkeypatch.setattr(auth_middleware, "load_active_session", slow_lookup)

    async def endpoint(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = auth_middleware.LocalBoundaryMiddleware(
        endpoint, hosts=["localhost"], origins=["http://localhost:3000"]
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            auth_settings_provider=lambda: settings,
            auth_session_factory=DummySession,
        )
    )

    async def call(cookie: bool) -> float:
        headers = [(b"host", b"localhost")]
        if cookie:
            headers.append((b"cookie", b"__Host-travel_session=synthetic"))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/v1/auth/session",
            "headers": headers,
            "app": app,
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message):
            return None

        start = asyncio.get_running_loop().time()
        await middleware(scope, receive, send)
        return asyncio.get_running_loop().time() - start

    async def run() -> tuple[float, float]:
        slow = asyncio.create_task(call(True))
        await asyncio.sleep(0.01)
        fast = await call(False)
        return fast, await slow

    fast_duration, slow_duration = asyncio.run(run())
    assert fast_duration < 0.08
    assert slow_duration >= 0.14
