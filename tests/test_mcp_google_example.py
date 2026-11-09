from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastmcp")

import httpx
from fastmcp import Client
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth import oauth_proxy
from fastmcp.server.auth.providers.google import GoogleProvider, GoogleTokenVerifier
from starlette.middleware import Middleware
from starlette.testclient import TestClient

from examples.mcp_server.server_google import AllowedGoogleAccount, HttpDiagnostics, create_server
from examples.mcp_server import example_search, server_google
from matlock2_mcp import SearchService
from tests.test_mcp import service


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


def test_http_diagnostics_log_status_without_request_data(
    configured: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = create_server(configured)
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        with TestClient(server.http_app(path="/mcp", middleware=[Middleware(HttpDiagnostics)])) as client:
            response = client.post(
                "/mcp?secret=query-value",
                headers={"Authorization": "Bearer header-value"},
                content="body-value",
            )
            assert response.status_code == 401
            assert client.get("/secret-path?secret=another-value").status_code == 404
            assert client.get("/.well-known/oauth-authorization-server").status_code == 200
    messages = [record.getMessage() for record in caplog.records if record.name == "uvicorn.error"]
    assert messages == [
        "MCP HTTP POST /mcp status=401",
        "MCP HTTP GET <other> status=404",
        "MCP HTTP GET /.well-known/oauth-authorization-server status=200",
    ]
    assert all(
        secret not in message
        for secret in ("query-value", "header-value", "body-value", "secret-path", "another-value")
        for message in messages
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_search_tools_and_semantic_query(
    configured: Path, service: SearchService, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    class FakeEmbedder:
        model_name = "test-model"
        dimensions = 2
        version = "test-embedder-v1"

        def __init__(self, model: str, dimensions: int, tokens: int) -> None:
            assert (model, dimensions, tokens) == ("BAAI/bge-small-en-v1.5", 384, 512)

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(text.split())), 1.0] for text in texts]

    monkeypatch.setattr(example_search, "FastEmbedder", FakeEmbedder)
    server = create_server(service.config.database_path, enable_semantic=enabled)

    async def exercise() -> None:
        async with Client(server) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert names == (
                {"metadata_search", "full_text_search", "get_distinct_metadata", "get_chunk"}
                | ({"semantic_search", "vector_search", "hybrid_knowledge_search"} if enabled else set())
            )
            if enabled:
                result = await client.call_tool(
                    "semantic_search", {"semantic_query": "database"}
                )
                assert result.structured_content["results"]
                result = await client.call_tool(
                    "hybrid_knowledge_search",
                    {"semantic_query": "database", "keyword_query": "migration"},
                )
                assert result.structured_content["results"]
                result = await client.call_tool(
                    "vector_search",
                    {"vector": [1.0, 1.0], "embedding_space": "example-bge-small-en-v1.5"},
                )
                assert result.structured_content["results"]

    asyncio.run(exercise())


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_passes_semantic_flag(
    configured: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    class FakeServer:
        def run(self, **kwargs: object) -> None:
            assert kwargs["transport"] == "http"
            assert kwargs["port"] == 8081
            assert kwargs["middleware"] is None

    def fake_create_server(database: Path, *, enable_semantic: bool = False) -> FakeServer:
        assert database == configured
        assert enable_semantic is enabled
        return FakeServer()

    monkeypatch.setattr(server_google, "create_server", fake_create_server)
    monkeypatch.setattr(
        sys, "argv",
        ["server_google.py", "--database", str(configured)]
        + (["--enable-semantic"] if enabled else []),
    )
    server_google.main()


def test_cli_enables_http_diagnostics(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeServer:
        def run(self, **kwargs: object) -> None:
            middleware = kwargs["middleware"]
            assert isinstance(middleware, list) and len(middleware) == 1
            assert isinstance(middleware[0], Middleware)
            assert middleware[0].cls is HttpDiagnostics

    monkeypatch.setattr(server_google, "create_server", lambda *args, **kwargs: FakeServer())
    monkeypatch.setattr(sys, "argv", ["server_google.py", "--http-diagnostics"])
    server_google.main()


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
