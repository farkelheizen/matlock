from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastmcp")

import httpx
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth import oauth_proxy
from fastmcp.server.auth.providers.google import GoogleProvider, GoogleTokenVerifier
from starlette.testclient import TestClient

from examples.mcp_server.server_google import AllowedGoogleAccount, create_server


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MATLOCK2_MCP_BASE_URL", "https://mcp.example.com")
    monkeypatch.setenv("MATLOCK2_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("MATLOCK2_GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("MATLOCK2_GOOGLE_ALLOWED_EMAILS", "owner@example.com, second@example.com")
    monkeypatch.setenv("MATLOCK2_MCP_SIGNING_KEY", "a" * 40)
    monkeypatch.setattr(oauth_proxy.settings, "home", tmp_path / "fastmcp")
    return tmp_path / "index.sqlite3"


@pytest.mark.parametrize(
    ("audience", "user", "allowed"),
    [
        ("google-client", {"email": "OWNER@example.com", "verified_email": True}, True),
        ("google-client", {"email": "second@example.com", "verified_email": True}, True),
        ("other-client", {"email": "owner@example.com", "verified_email": True}, False),
        ("google-client", {"email": "other@example.com", "verified_email": True}, False),
        ("google-client", {"email": "owner@example.com", "verified_email": False}, False),
        ("google-client", {"email": "owner@example.com"}, False),
        ("google-client", {}, False),
        ("google-client", None, False),
    ],
)
def test_authorizes_only_verified_allowed_account(
    configured: Path, monkeypatch: pytest.MonkeyPatch, audience: str, user: object, allowed: bool
) -> None:
    server = create_server(configured)
    assert isinstance(server.auth, AllowedGoogleAccount)
    access = AccessToken(
        token="test", client_id=audience, scopes=["openid"], claims={"google_user_data": user}
    )
    monkeypatch.setattr(GoogleProvider, "load_access_token", AsyncMock(return_value=access))
    result = asyncio.run(server.auth.load_access_token("test"))
    assert (result is access) is allowed


def test_unauthenticated_http_and_discovery(configured: Path) -> None:
    server = create_server(configured)
    with TestClient(server.http_app(path="/mcp")) as client:
        response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 401
        assert "resource_metadata" in response.headers["www-authenticate"]
        metadata_url = response.headers["www-authenticate"].split('resource_metadata="')[1].split('"')[0]
        metadata = client.get(metadata_url)
        assert metadata.status_code == 200
        assert metadata.json()["authorization_servers"] == ["https://mcp.example.com/"]


def test_google_verifier_claims_support_allowlist(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = create_server(configured)
    assert isinstance(server.auth, AllowedGoogleAccount)

    def google_response(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tokeninfo"):
            return httpx.Response(200, json={
                "audience": "google-client",
                "scope": "openid https://www.googleapis.com/auth/userinfo.email",
                "expires_in": 3600,
            })
        assert request.url.path.endswith("/userinfo")
        return httpx.Response(200, json={
            "id": "google-user-id", "email": "second@example.com", "verified_email": True,
        })

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(google_response)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))

    async def verify_upstream(_self: GoogleProvider, token: str) -> AccessToken | None:
        return await GoogleTokenVerifier(
            required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"]
        ).verify_token(token)

    monkeypatch.setattr(GoogleProvider, "load_access_token", verify_upstream)
    result = asyncio.run(server.auth.load_access_token("test"))
    assert result is not None
    assert result.claims["google_user_data"]["email"] == "second@example.com"


@pytest.mark.parametrize(
    ("email", "status"),
    [("owner@example.com", 200), ("second@example.com", 200), ("other@example.com", 401)],
)
def test_http_requests_enforce_allowlist(
    configured: Path, monkeypatch: pytest.MonkeyPatch, email: str, status: int
) -> None:
    server = create_server(configured)
    access = AccessToken(
        token="test",
        client_id="google-client",
        scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        claims={"google_user_data": {"email": email, "verified_email": True}},
    )
    monkeypatch.setattr(GoogleProvider, "load_access_token", AsyncMock(return_value=access))
    with TestClient(server.http_app(path="/mcp")) as client:
        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer test", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == status


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MATLOCK2_GOOGLE_ALLOWED_EMAILS", ""),
        ("MATLOCK2_GOOGLE_ALLOWED_EMAILS", "owner@example.com,"),
        ("MATLOCK2_GOOGLE_ALLOWED_EMAILS", "*@example.com"),
        ("MATLOCK2_GOOGLE_ALLOWED_EMAILS", "not-an-email"),
        ("MATLOCK2_GOOGLE_CLIENT_SECRET", ""),
        ("MATLOCK2_MCP_SIGNING_KEY", "short"),
        ("MATLOCK2_MCP_BASE_URL", "http://example.com"),
        ("MATLOCK2_MCP_BASE_URL", "https://example.com/mcp"),
    ],
)
def test_invalid_configuration_fails_closed(
    configured: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        create_server(configured)
