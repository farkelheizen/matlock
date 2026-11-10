from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from examples.mcp.example_policy import ExampleSearchPolicy
from matlock_mcp.policy import PolicyDecision
from matlock_mcp.search import SearchService


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    pytest.importorskip("fastmcp")
    from fastmcp.server.auth import oauth_proxy

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
        ("google-client", {"email": "other@example.com", "verified_email": True}, False),
        ("other-client", {"email": "owner@example.com", "verified_email": True}, False),
        ("google-client", {"email": "owner@example.com", "verified_email": False}, False),
        ("google-client", None, False),
    ],
)
def test_authorizes_only_verified_allowed_account(
    configured, monkeypatch: pytest.MonkeyPatch, audience: str, user: object, allowed: bool
) -> None:
    from fastmcp.server.auth.auth import AccessToken
    from fastmcp.server.auth.providers.google import GoogleProvider

    from examples.mcp.google_oidc_host import AllowedGoogleAccount, create_server

    server = create_server(configured)
    assert isinstance(server.auth, AllowedGoogleAccount)
    access = AccessToken(
        token="test", client_id=audience, scopes=["openid"], claims={"google_user_data": user}
    )
    monkeypatch.setattr(
        GoogleProvider, "load_access_token", lambda _self, _token: _verified(access)
    )
    result = asyncio.run(server.auth.load_access_token("test"))
    assert (result is access) is allowed


async def _verified(access):
    return access


def test_unauthenticated_http_and_discovery(configured) -> None:
    from starlette.testclient import TestClient

    from examples.mcp.google_oidc_host import create_server

    server = create_server(configured)
    with TestClient(server.http_app(path="/mcp")) as client:
        response = client.post("/mcp", headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
        metadata = client.get(metadata_url)
        assert metadata.status_code == 200
        assert metadata.json()["authorization_servers"] == ["https://mcp.example.com/"]


def test_example_policy_hooks() -> None:
    policy = ExampleSearchPolicy()
    assert policy.rewrite("full_text_search", {"limit": 100}) == {"limit": 25}
    assert policy.rewrite("full_text_search", {"limit": -1}) == {"limit": -1}
    assert policy.document({"metadata": {policy.hidden_marker: True}}) == PolicyDecision("hide")
    assert policy.document({"metadata": {}}) == PolicyDecision("allow")
    assert policy.chunk({"attributes": {policy.hidden_marker: True}}) == PolicyDecision("hide")
    assert policy.chunk({"attributes": {}}) == PolicyDecision("allow")


def test_http_host_wires_search_policy(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    from examples.mcp import google_oidc_host

    registered: dict[str, object] = {}
    monkeypatch.setattr(
        google_oidc_host,
        "register_tools",
        lambda _server, service: registered.update(service=service),
    )
    google_oidc_host.create_server(configured)
    service = registered["service"]
    assert isinstance(service, SearchService)
    assert isinstance(service.policy, ExampleSearchPolicy)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MATLOCK2_MCP_BASE_URL", "http://mcp.example.com"),
        ("MATLOCK2_MCP_BASE_URL", "https://mcp.example.com/mcp"),
        ("MATLOCK2_MCP_SIGNING_KEY", "short"),
        ("MATLOCK2_GOOGLE_ALLOWED_EMAILS", "*@example.com"),
    ],
)
def test_invalid_configuration_fails_closed(configured, monkeypatch, name: str, value: str) -> None:
    from examples.mcp.google_oidc_host import create_server

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        create_server(configured)
