# Modular MCP search tools

The optional `matlock2_mcp` package adds read-only search tools to an existing
FastMCP 2 application. It does not start a server, ingest files, watch a vault,
or require an ingestion configuration. The host chooses the transport, lifecycle,
authentication, authorization, logging, and which tools to expose. Only the
host should decide which users can access the index: these tools do not
implement per-user or per-document authorization.

## Install and register

```sh
python -m pip install -e '.[mcp]'
```

For example, add tools to an existing server without semantic search:

```python
from pathlib import Path
from fastmcp import FastMCP
from matlock2_mcp import SearchConfig, SearchService
from matlock2_mcp.registration import register_tools

mcp = FastMCP("My application")
search = SearchService(SearchConfig(database_path=Path("/path/to/index.sqlite3")))
register_tools(mcp, search)
# The existing application runs mcp using its own transport.
```

`register_tools(mcp, search, enabled={"full_text_search", "get_chunk"})`
exposes only the named tools. By default, metadata, FTS, discovery and chunk
lookup are registered. Vector search is also registered if an embedding space
is configured, and semantic/hybrid tools if a matching embedder is supplied.
Requesting unavailable or unknown tools at registration raises an error.

The standalone service methods may also be used without importing FastMCP.
Each request opens an existing database in read-only mode with a bounded busy
timeout and a short read transaction. Schema version 1 is required. Vector
requests load sqlite-vec per connection; searches never create or migrate a
database. Readers see a consistent snapshot during each request. Errors are
reported rather than silently converted into empty results.

## Tools

Results use vault-relative `source_file`, `chunk_id`, document metadata and
chunk content where applicable. Content is capped at `max_content_chars` and
`content_truncated` indicates truncation. Chunk IDs identify path and ordinal,
not an immutable revision. The default limit is 10; `SearchConfig.max_limit`
defaults to 50. Metadata browsing and discovery use offset pagination
(`next_offset` is null at the end); a changing index can cause duplicate or
omitted entries between calls. Offsets are limited to 10,000;
`pagination_truncated` indicates additional results beyond that bound.

| Tool | Purpose |
| --- | --- |
| `metadata_search` | Return matching documents; empty filters browse in path order. |
| `full_text_search` | Return matching chunks for a literal FTS phrase. Lower `score` is better. |
| `semantic_search` | Embed text with the configured provider and rank by cosine distance. |
| `vector_search` | Rank a supplied vector and declared `embedding_space` by cosine distance. |
| `hybrid_knowledge_search` | Combine literal keyword and semantic searches by reciprocal-rank fusion. |
| `get_distinct_metadata` | List scalar values for a dot-separated field, including explicit null but excluding missing fields and objects/arrays. |
| `get_chunk` | Retrieve a chunk by its 24-character ID, or return null if absent. |

Searches accept optional `metadata_filters`, a mapping of field names to scalar
values or operator objects. Fields are dot-separated identifiers; filters are
AND-ed. Scalar values mean equality. Examples:

```json
{
  "project_id": "atlas",
  "status": {"op": "in", "value": ["accepted", "in-progress"]},
  "priority": {"op": "gte", "value": 2},
  "archived": {"op": "exists", "value": false}
}
```

Supported operators are `eq`, `in`, `lt`, `lte`, `gt`, `gte`, `exists`, and
`is_null` (the last requires `"value": true`). Values for comparisons must be
non-null scalars; equality and range comparisons distinguish JSON integer,
real, text and boolean types. `in` matches any listed scalar, not membership
within a metadata array. `exists` distinguishes a missing field from an
explicit null. Array and object values are not searchable by these operators.
Queries, filters, limits and offsets are bounded and values are SQL parameters.

FTS input is treated as a literal phrase, not raw FTS syntax. `score` is
SQLite BM25; vector `distance` is cosine distance, with smaller values
indicating closer matches. Filters are applied before ranking. Vector search
scans the filtered vectors exactly, which may be slow for large indexes.
Hybrid search combines the first `candidate_limit` matches from each modality
using `1/(60 + rank)` per modality. Its higher `relevance_score` is better, and its
`truncated` flag reports if output or a candidate window was cut off. It does
not silently fall back if either modality fails.

## Semantic compatibility

The current index records the model name, package version and dimensions, but
not a pinned model artifact or preprocessing identity. Operators must verify
and attest that the query provider uses the same artifact and preprocessing
as ingestion. Configure all of `embedding_space` (an operator-chosen identity),
`embedding_model`, `embedding_version`, and `embedding_dimensions`. A supplied
vector must declare that exact space. Semantic tools additionally need an
embedder exposing `model_name`, `version`, `dimensions`, and
`embed(list[str])`. The service rejects model/version mixtures, mismatched
providers, dimensions and invalid query vectors. These checks cannot prove
the artifact identity; rebuild the index or disable semantic tools when in
doubt. Metadata and FTS remain available independently.

The local FastEmbed adapter can be reused as a provider:

```python
from pathlib import Path
from matlock2.embedding import FastEmbedder
from matlock2_mcp import SearchConfig, SearchService

provider = FastEmbedder("BAAI/bge-small-en-v1.5", 384, 512)
config = SearchConfig(
    database_path=Path("/path/to/index.sqlite3"),
    embedding_space="my-attested-bge-space",
    embedding_model=provider.model_name,
    embedding_version=provider.version,
    embedding_dimensions=provider.dimensions,
)
search = SearchService(config, provider)
```

This assumes the same model artifact and preprocessing were used to build the
index; matching dimensions and versions alone are not proof.

## Separate example host

Run the [fictional vault scan](example.md) first, then from the repository root:

```sh
python -m pip install -e '.[mcp]'
python examples/mcp_server/server.py
```

The example runs an MCP **stdio** server; connect an MCP client using its
supported stdio configuration. Enable semantic, vector and hybrid tools with:

```sh
python examples/mcp_server/server.py --enable-semantic
```

That flag attests the example database was built using the default model and
the same artifact and preprocessing available to the host. A model download
may be required. `--database` selects a different index; do not make that
attestation for an index whose embedding provenance is unknown. FastMCP's
Python client and stdio transport are covered by integration tests; Gemini
GUI integration and its particular transport configuration have not been
verified.

## Google-authenticated HTTP example

[`examples/mcp_server/server_google.py`](../examples/mcp_server/server_google.py)
is a separate, read-only HTTP host for the example index. It uses FastMCP's
Google OAuth provider and accepts only explicitly allowed, Google-verified
email addresses. All allowed accounts have access to the same index and its
registered tools; this is not per-document authorization. Authentication and
discovery were tested with FastMCP 2.14.7; an actual Gemini connection has not
yet been verified.

1. Follow the [fictional vault scan](example.md) and install the optional MCP
   dependencies with `python -m pip install -e '.[mcp]'`.
2. In Google Cloud, configure a Web application OAuth client and add the exact
   authorized redirect URI `https://mcp.example.com/auth/callback`, replacing
   the hostname with your public hostname. Configure the Google consent screen;
   if the app is in testing mode, add each allowed Google account as a test user.
3. Configure Cloudflare Tunnel to route the entire hostname, including
   `/.well-known/`, OAuth routes, and `/mcp`, to `http://localhost:8081`.
   Do not rely on Tunnel alone for authentication. A browser-oriented
   Cloudflare Access login in front of these routes may interfere with MCP
   discovery and bearer-token requests.
4. On macOS, create a private `.env` file in the repository root. If you already
   have a `.env` file there, back it up and add the settings below instead of
   replacing it. The repository ignores `.env`, but it still contains secrets:
   don't commit or share it.

   ```sh
   touch .env
   chmod 600 .env
   open -e .env
   ```

   Add these shell-compatible settings in TextEdit, replacing the placeholders
   with your own values. Use the public HTTPS origin without `/mcp`, and list
   allowed Google email addresses separated by commas; wildcards are not
   supported.

   ```sh
   MATLOCK2_MCP_BASE_URL='https://mcp.example.com'
   MATLOCK2_GOOGLE_ALLOWED_EMAILS='you@example.com,another@example.com'
   MATLOCK2_GOOGLE_CLIENT_ID='your-google-client-id'
   MATLOCK2_GOOGLE_CLIENT_SECRET='your-google-client-secret'
   MATLOCK2_MCP_SIGNING_KEY='your-generated-signing-key'
   FASTMCP_HOME="$HOME/.local/share/matlock2-google-mcp"
   ```

   Generate the signing key with `openssl rand -base64 32`, paste the output
   into the file, and save it. The key must be at least 32 characters and
   generated from high-entropy randomness. Reuse it across restarts. Store
   backups of the file securely.

   From the repository root, activate the virtual environment and load the
   file before starting the server:

   ```sh
   . .venv/bin/activate
   set -a
   . ./.env
   set +a
   python examples/mcp_server/server_google.py
   ```

   Sourcing `.env` executes shell syntax, so only source a file you trust.
   Reload it after changing settings and restart the server. FastMCP stores encrypted
   OAuth state under `FASTMCP_HOME`; restrict access to that directory. Rotating
   the signing key invalidates existing tokens and can make stored OAuth state
   unreadable, requiring clients to reconnect and state to be reset deliberately.
   `--database /path/to/index.sqlite3` selects another existing index. The
   example exposes metadata and full-text tools, not semantic tools.

5. Connect an MCP client to `https://mcp.example.com/mcp`. An unauthenticated
   MCP request should receive HTTP 401 with OAuth discovery information. The
   server listens only on `127.0.0.1:8081`; keep it and `cloudflared` running.
   Sign in with an allowed Google account. An unlisted or unverified account
   is denied when it tries to use the MCP endpoint, even if Google sign-in
   succeeds. Restart after changing the allowlist.

The Google client ID and secret above identify **this server to Google**. They
are not automatically the credentials requested by a downstream MCP client's
GUI. FastMCP's OAuth proxy supports dynamic client registration; whether the
Gemini GUI uses a compatible registration flow or requires separately
pre-registered credentials remains to be verified. Do not paste the Google
client secret into Gemini's client-secret field as a substitute.

The public endpoint remains sensitive even with authentication. Only authorize
MCP clients you trust, keep the host and OAuth dependencies updated, and
restrict who can access the local machine and OAuth state. The example disables
HTTP access logs and HTTPX informational logs to avoid logging OAuth query
parameters; review logging at Cloudflare and any other proxies separately.
Queries, metadata, and returned content are sent to the connected client.

The database, WAL, SHM, queries, returned content and metadata can contain
private information. Restrict filesystem and transport access. Do not expose
the host over a network without appropriate authentication and authorization,
and do not log payloads or send text to a remote embedding provider without
the operator's consent. The example uses a local model and stdio.
