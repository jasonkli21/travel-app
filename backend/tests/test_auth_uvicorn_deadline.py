"""Run the synthetic OAuth callback over Uvicorn's real event loops."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


@pytest.mark.parametrize("loop", ["auto", "asyncio"])
def test_mounted_callback_uses_shared_elapsed_budget(
    database_engine: Engine, clean_database: None, loop: str
) -> None:
    with database_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert schema is not None
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(
        {
            "SYNTHETIC_IDENTITY_FIXTURE": "1",
            "SYNTHETIC_UVICORN_LOOP": loop,
            "SYNTHETIC_API_PORT": str(port),
            "SYNTHETIC_CALLBACK_DEADLINE_SECONDS": "1",
            "SYNTHETIC_SLOW_EXCHANGE_SECONDS": "2",
            "SYNTHETIC_TEST_SCHEMA": schema,
            "TRAVEL_AUTH_MODE": "google_oidc",
            "GOOGLE_OAUTH_CLIENT_ID": "travel-client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "synthetic-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://127.0.0.1/auth/google/callback",
            "GOOGLE_OAUTH_ALLOWED_EMAIL": "owner@gmail.com",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        }
    )
    fixture = Path(__file__).parent / "fixtures" / "synthetic_identity_server.py"
    process = subprocess.Popen(
        [sys.executable, str(fixture)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=4, trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError(f"Synthetic Uvicorn failed: {process.stderr.read()!r}")
                try:
                    if client.get("/health").status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(0.03)
            else:
                raise AssertionError("Synthetic Uvicorn did not start")

            def callback(code: str) -> httpx.Response:
                start = client.get("/v1/auth/google/start")
                assert start.status_code == 200, start.text
                state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
                flow = start.cookies["__Host-travel_oauth_flow"]
                return client.post(
                    "/v1/auth/google/callback",
                    headers={"Cookie": f"__Host-travel_oauth_flow={flow}"},
                    json={"code": code, "state": state},
                )

            assert callback("synthetic-code").status_code == 200
            started = time.monotonic()
            slow = callback("synthetic-slow-code")
            elapsed = time.monotonic() - started
            assert slow.status_code == 503, slow.text
            assert elapsed < 1.8
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stderr is not None:
            process.stderr.close()
