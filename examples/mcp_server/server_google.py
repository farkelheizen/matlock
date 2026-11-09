"""Google-authenticated HTTP host for the fictional second-brain index."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from matlock2_mcp import SearchConfig, SearchService
from matlock2_mcp.registration import register_tools


class AllowedGoogleAccount(GoogleProvider):
    def __init__(self, *, allowed_emails: frozenset[str], google_client_id: str, **kwargs: str) -> None:
        super().__init__(
            client_id=google_client_id,
            required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
            **kwargs,
        )
        self.allowed_emails = allowed_emails
        self.google_client_id = google_client_id

    async def load_access_token(self, token: str) -> AccessToken | None:
        verified = await super().load_access_token(token)
        if verified is None:
            return None
        user = verified.claims.get("google_user_data")
        if (
            verified.client_id != self.google_client_id
            or not isinstance(user, dict)
            or user.get("verified_email") is not True
            or not isinstance(user.get("email"), str)
            or user["email"].casefold() not in self.allowed_emails
        ):
            logging.getLogger(__name__).warning("Google account authorization denied")
            return None
        return verified


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def create_server(database: Path) -> FastMCP:
    base_url = required_env("MATLOCK2_MCP_BASE_URL")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MATLOCK2_MCP_BASE_URL must be an HTTPS origin without a path")

    signing_key = required_env("MATLOCK2_MCP_SIGNING_KEY")
    if len(signing_key) < 32:
        raise ValueError("MATLOCK2_MCP_SIGNING_KEY must be at least 32 characters")

    emails = required_env("MATLOCK2_GOOGLE_ALLOWED_EMAILS").split(",")
    allowed_emails = frozenset(email.strip().casefold() for email in emails)
    if any(
        email.count("@") != 1
        or not all(email.split("@"))
        or any(char.isspace() for char in email)
        or "*" in email
        for email in allowed_emails
    ):
        raise ValueError("MATLOCK2_GOOGLE_ALLOWED_EMAILS must be a comma-separated list of email addresses")

    auth = AllowedGoogleAccount(
        google_client_id=required_env("MATLOCK2_GOOGLE_CLIENT_ID"),
        client_secret=required_env("MATLOCK2_GOOGLE_CLIENT_SECRET"),
        allowed_emails=allowed_emails,
        base_url=base_url.rstrip("/"),
        jwt_signing_key=signing_key,
    )
    server = FastMCP("Matlock2 Google example", auth=auth)
    register_tools(server, SearchService(SearchConfig(database_path=database)))
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "second-brain" / ".matlock2" / "index.sqlite3",
    )
    args = parser.parse_args()
    # Google's token verification sends the access token in a URL query.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = create_server(args.database)
    server.run(
        transport="http",
        host="127.0.0.1",
        port=8081,
        path="/mcp",
        show_banner=False,
        uvicorn_config={"access_log": False},
    )


if __name__ == "__main__":
    main()
