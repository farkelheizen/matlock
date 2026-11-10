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
Each request opens an existing database through the shared `matlock2_index`
contract in read-only mode with a bounded busy timeout and a short read
transaction. Schema version 2 is required. Vector
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

## Attributes and policies

See [content attributes](attributes.md) for indexing, migration, and the
separate writer workflow. Configure `SearchConfig.document_attribute_keys`
and `chunk_attribute_keys` as tuples of top-level keys permitted in responses
and filters. Both default to empty tuples; other keys remain available to
trusted policy code but are not returned. A missing attribute row is returned
as `null`, an existing row as an object containing only permitted keys.
Document results add `document_attributes`; chunk results add that field and
`chunk_attributes`.

The document search tool accepts `document_attribute_filters`; chunk searches
accept that parameter and `chunk_attribute_filters`. Each uses the same
operators and type rules as metadata filters. Filters are AND-ed, are applied
before limits and ranking, and reject paths whose first component is not an
exposed key. Discovery continues to expose metadata, not attribute values.
The host's rewrite hook can add filters on unexposed keys; these filters are
trusted policy constraints, not client-accessible fields. A host must not
derive such constraints from untrusted input in ways that reveal private data.

Hosts may pass a trusted `SearchPolicy` object as `policy` to `SearchService`.
Its `rewrite(tool, arguments)` method receives the tool name and a dictionary
of arguments and returns a dictionary with the same keys; validation runs
afterward. Its `document(record)` and `chunk(record)` methods return a
`PolicyDecision`. Records contain the exact indexed content, its `sha256`,
source identity, metadata, and complete attributes (or `None` when absent);
chunk records also contain `document_attributes`. The policy receives no
automatic per-request authentication context: a host needing such context
must supply an appropriately scoped policy/service and avoid sharing mutable
request state across concurrent clients.

```python
from matlock2_mcp import PolicyDecision, SearchConfig, SearchService

class ReviewedOnly:
    def rewrite(self, tool, arguments):
        return arguments

    def document(self, record):
        attributes = record["attributes"] or {}
        return PolicyDecision("allow" if attributes.get("reviewed") is True else "hide")

    def chunk(self, record):
        attributes = record["attributes"] or {}
        return PolicyDecision("allow" if attributes.get("reviewed") is True else "hide")

search = SearchService(SearchConfig(database_path=database_path), policy=ReviewedOnly())
```

This example shows a fail-closed policy but performs no evaluation. A policy
can return `allow`, `hide`, or `null` for content; a document decision
applies to all its chunks. `PolicyDecision` also accepts `disclose_metadata`,
`disclose_source`, and `disclose_attributes` booleans. A false value emits
the corresponding field as `null`; hidden metadata and attributes cannot be
used by client filters for that result. Metadata discovery excludes documents
whose policy hides metadata. Hidden documents are absent from discovery and
search; a hidden chunk lookup returns the same `null` as an absent chunk.
Document-only browsing uses the document decision even when all its chunks
are hidden. A visible document or chunk may still expose its identifier,
score, or other fields unless the host chooses an appropriate policy.

Decisions run against a consistent database snapshot, before candidate
limits and pagination. With any policy configured, full-text ordering uses
path and chunk ordinal instead of BM25 and returns a null FTS score: BM25's
corpus statistics would otherwise include hidden documents. Hybrid search
uses that ordering for its FTS rank. Policy exceptions and invalid decisions
fail the request, rather than allowing results. With no policy, existing
visibility and BM25 behavior remain unchanged; this is **not** a privacy
screen. A `null` content decision is also not a complete secrecy boundary:
search matches, rankings, IDs, paths, metadata, and other fields can reveal
information about content unless the policy hides the result instead. Policies
are trusted code, not a sandbox or a replacement for host authentication
and authorization.

`matlock2_mcp.SecretScanPolicy` is a ready-made policy that hides content
flagged by the [secret scanner](secret-scanner.md#hiding-risky-content-from-mcp-search).

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
from matlock2_ingest.embedding import FastEmbedder
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
GUI integration with this stdio host has not been verified.

## Google-authenticated HTTP example

[`examples/mcp_server/server_google.py`](../examples/mcp_server/server_google.py)
is a separate, read-only HTTP host for the example index. It uses FastMCP's
Google OAuth provider and accepts only explicitly allowed, Google-verified
email addresses. All allowed accounts have access to the same index and its
registered tools; this is not per-document authorization. Authentication and
discovery were tested with FastMCP 2.14.7, and a user has reported connecting
this host to the Gemini GUI. Other Gemini or FastMCP versions may behave
differently.

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
   example exposes metadata and full-text tools by default.

5. In Gemini's custom connected app dialog, enter
   `https://mcp.example.com/mcp` as the MCP Server URL, using your own
   hostname. Try leaving the optional client ID and client secret blank so
   Gemini can register with FastMCP. Follow the consent and Google sign-in
   prompts, using an allowed Google account. Keep the server and `cloudflared`
   running: the server listens only on `127.0.0.1:8081`. An unlisted or
   unverified account is denied when it tries to use the MCP endpoint, even
   if Google sign-in succeeds. Restart after changing the allowlist.

The Google client ID and secret in `.env` identify **FastMCP to Google**.
They are not the credentials for Gemini as an MCP client of FastMCP. Do not
paste the Google credentials into Gemini's optional client fields. If Gemini
displays its own redirect URL, that is the callback from FastMCP to Gemini;
the Google Cloud OAuth client's redirect URI remains the FastMCP
`/auth/callback` URL from step 2.

### Connection troubleshooting

If Gemini says the server is invalid, make sure the server URL ends in `/mcp`:
the example does not serve MCP at the domain root. With the server running, run
these checks in another terminal, replacing `mcp.example.com` with your
hostname:

```sh
curl -i -X POST -H 'Accept: application/json, text/event-stream' \
  http://127.0.0.1:8081/mcp
curl -i -X POST -H 'Accept: application/json, text/event-stream' \
  https://mcp.example.com/mcp
curl -i https://mcp.example.com/.well-known/oauth-protected-resource/mcp
curl -i https://mcp.example.com/.well-known/oauth-authorization-server
```

Both POST requests should return **401** with a `WWW-Authenticate` header
containing a resource metadata URL; this is expected before authentication.
Both discovery requests should return **200** and JSON. If the local request
works but the public requests do not, check the tunnel's hostname, routing,
and any Cloudflare Access policies or path rewrites. The server disables HTTP
access logs to avoid logging OAuth query parameters.

If the consent page says **Client Not Registered**, remove the failed
connection in Gemini and retry with the client ID and secret fields blank.
FastMCP's OAuth proxy maintains a separate registry for MCP clients; the
upstream Google Cloud client ID is not a registered MCP client ID. If this
still fails, check whether your Gemini client supports dynamic registration
with this FastMCP version. Do not share client secrets, tokens, authorization
codes, or full OAuth URLs when troubleshooting.

For more detailed FastMCP logs, stop the server, load `.env` as described above,
and start it with:

```sh
FASTMCP_LOG_LEVEL=DEBUG python examples/mcp_server/server_google.py --enable-semantic
```

Omit `--enable-semantic` if semantic search is not enabled. Debug output can
contain sensitive OAuth details, client identifiers, authorization codes, or
tool-related data. Review and redact it before sharing. Restart without
`FASTMCP_LOG_LEVEL=DEBUG` to return to the default log level. This setting does
not enable HTTP access logs or HTTPX informational logs; the example disables
those to reduce the risk of logging OAuth credentials.

For HTTP routing diagnostics without full access logs, start the server with:

```sh
python examples/mcp_server/server_google.py --enable-semantic --http-diagnostics
```

Omit `--enable-semantic` if not needed. This opt-in logs the HTTP method,
response status and a fixed known route name (unknown paths appear as
`<other>`). It does not log query strings, headers, request or response bodies,
or client addresses. Response statuses are logged when headers are sent, not
when a streaming response finishes. Other components' logs, including
FastMCP debug logs if enabled separately, may still contain sensitive data;
review logs before sharing. Stop and restart without the flag to turn these
diagnostics off.

### Semantic search in the Google example

To enable semantic, vector, and hybrid search, stop the server, load `.env` as
described above, and restart it with:

```sh
python examples/mcp_server/server_google.py --enable-semantic
```

The flag attests the index was built with the default
`BAAI/bge-small-en-v1.5` model and the same model artifact and preprocessing
available to this host, as in the stdio example. A model download may be
required on first use. With `--database`, enable the flag only if you have
verified that the selected index has compatible embedding provenance.
See [Semantic compatibility](#semantic-compatibility): matching model names,
versions and dimensions alone cannot establish compatibility. The embedding
model runs locally; queries and tool results are still sent to the connected
client. If Gemini does not show the additional tools after restart, reconnect
the app to refresh its tool list.

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
the operator's consent. The stdio example uses a local model when semantic
search is enabled.
