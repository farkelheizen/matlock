# MCP integration

Matlock provides optional, importable search tools for a host-owned MCP
application, not a supported Matlock MCP service. The runnable examples are
minimal local stdio and Google-authenticated HTTP hosts; neither is a supported
deployment target. An embedding application owns server creation, transport,
authentication and authorization, logging, configuration, and lifecycle.

## Install and register

Install the optional integration:

```sh
python -m pip install -e '.[mcp]'
```

Register tools on an existing FastMCP instance; the host starts and serves its
own application:

```python
from pathlib import Path
from fastmcp import FastMCP
from matlock_mcp import SearchConfig, SearchService
from matlock_mcp.registration import register_tools

mcp = FastMCP("My application")
search = SearchService(SearchConfig(database_path=Path("/path/to/index.sqlite3")))
register_tools(mcp, search)
```

By default, registration adds only metadata search, full-text search, metadata
discovery, and chunk lookup. Semantic, vector, and hybrid search are optional:
pass them explicitly through `enabled` when the service has compatible
embedding configuration and, for semantic and hybrid search, a query embedder.
Unknown tools or unavailable embedding capabilities raise an error.

The service methods can also be called without importing FastMCP. They open an
existing schema-version-2 database read-only through the shared
`matlock_index` contract. Searches do not create, migrate, or write to the
index. Errors are reported rather than silently converted to empty results.

## Tools and results

| Tool | Purpose |
| --- | --- |
| `metadata_search` | Return matching documents; empty filters browse in path order. |
| `full_text_search` | Return chunks matching a literal FTS phrase. Lower `score` is better. |
| `get_distinct_metadata` | List scalar values for a dot-separated field, including explicit null. |
| `get_chunk` | Retrieve a chunk by its 24-character ID, or return null if absent. |
| `semantic_search` | Optional: embed text and rank by cosine distance. |
| `vector_search` | Optional: rank a supplied vector and declared `embedding_space`. |
| `hybrid_knowledge_search` | Optional: combine keyword and semantic results by reciprocal-rank fusion. |

Results use vault-relative source paths and include document metadata and chunk
content where applicable. Content is capped by `max_content_chars`, with
`content_truncated` indicating truncation. Chunk IDs identify path and ordinal,
not an immutable revision. The default result limit is 10;
`SearchConfig.max_limit` defaults to 50. Metadata browsing and discovery use
offset pagination; a changing index can cause duplicate or omitted entries
between calls.

Searches accept optional `metadata_filters`, a mapping of field names to scalar
values or operator objects. Fields are dot-separated identifiers and filters
are AND-ed. Supported operators are `eq`, `in`, `lt`, `lte`, `gt`, `gte`,
`exists`, and `is_null` (which requires `"value": true`). Values are SQL
parameters; queries, filters, limits, and offsets are bounded. For example:

```json
{
  "project_id": "atlas",
  "status": {"op": "in", "value": ["accepted", "in-progress"]},
  "priority": {"op": "gte", "value": 2},
  "archived": {"op": "exists", "value": false}
}
```

## Host policies and hooks

Hosts may pass a trusted `SearchPolicy` to `SearchService`. Its
`rewrite(tool, arguments)` hook can adjust validated query arguments; its
`document(record)` and `chunk(record)` hooks return a `PolicyDecision` to
allow, hide, or null content and optionally suppress metadata, source paths,
or attributes. Document decisions apply to all its chunks. Policy failures
fail the request rather than exposing unfiltered results.

Policies receive no automatic per-request authentication context. A host that
needs user-specific access must provide an appropriately scoped policy/service
and avoid sharing mutable request state between concurrent clients. These
hooks are trusted Python code, not a sandbox or replacement for host
authentication and authorization.

The `SearchConfig.document_attribute_keys` and `chunk_attribute_keys` settings
control which top-level attribute keys can be returned or filtered by clients.
Other keys remain available to trusted policy code. See
[content attributes](attributes.md) for indexing and the separate writer
workflow.

`SecretScanPolicy` is a ready-made policy that hides content flagged by the
[audit feature](secret-scanner.md). By default it fails closed for content
without an audit result, malformed results, risky results, and oversize items.

Without a policy, search visibility is not an authorization boundary. Search
matches, rankings, IDs, paths, metadata, or other fields may reveal
information. With policies, FTS ordering avoids corpus statistics from hidden
documents and policy exceptions do not return results.

## Semantic compatibility

An index records model name, package version, and dimensions, but not a pinned
model artifact or preprocessing identity. Operators must verify that the
query provider matches ingestion. Configure `embedding_space`,
`embedding_model`, `embedding_version`, and `embedding_dimensions` in
`SearchConfig`; a supplied vector must declare the same embedding space.
Semantic tools additionally require an embedder exposing `model_name`,
`version`, `dimensions`, and `embed(list[str])`. These checks cannot prove
artifact identity; rebuild the index or disable semantic tools when in doubt.
Metadata and full-text tools remain independently available.

## Minimal local example

The local stdio example creates a tiny FastMCP application only to demonstrate
registration. It is not a supported Matlock server or a separately deployable
service. Run the [fictional vault example](example.md) first, then:

```sh
python -m pip install -e '.[mcp]'
python examples/mcp/embedded_host.py
```

It uses local stdio for this example process. To demonstrate optional vector,
semantic, and hybrid tools, run:

```sh
python examples/mcp/embedded_host.py --enable-semantic
```

That flag assumes the selected index was built with the same model artifact
and preprocessing used by the example. A model download may be required.
`--database` selects another existing index; do not enable semantic tools
unless its embedding provenance is verified. For real applications, import
`register_tools` into the host that owns the application's MCP lifecycle.

## Google-authenticated HTTP example

The optional HTTP example restores the former Google OIDC demonstration. It
binds to `127.0.0.1:8081/mcp` and is intended to sit behind a TLS-terminating
reverse proxy or tunnel. Create the second-brain index first, then install the
MCP extra:

```sh
python -m pip install -e '.[mcp]'
```

Provide these settings through the process environment or a secret manager;
do not commit credentials:

| Variable | Purpose |
| --- | --- |
| `MATLOCK_MCP_BASE_URL` | Public HTTPS origin for the host, without a path, query, or fragment. |
| `MATLOCK_MCP_SIGNING_KEY` | Random signing key of at least 32 characters. |
| `MATLOCK_GOOGLE_CLIENT_ID` | Google OAuth client ID. |
| `MATLOCK_GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `MATLOCK_GOOGLE_ALLOWED_EMAILS` | Comma-separated allowlist of Google email addresses. |

For example, export these variables in the shell that starts the host, using
your own values:

```sh
export MATLOCK_MCP_BASE_URL="https://<public-origin>"
export MATLOCK_MCP_SIGNING_KEY="<random-secret-at-least-32-characters>"
export MATLOCK_GOOGLE_CLIENT_ID="<google-oauth-client-id>"
export MATLOCK_GOOGLE_CLIENT_SECRET="<google-oauth-client-secret>"
export MATLOCK_GOOGLE_ALLOWED_EMAILS="<allowed-account@example.com>"
```

If you use `dsec`, this repository includes
[`examples/mcp/google_oidc_host.dsec`](../examples/mcp/google_oidc_host.dsec),
which maps the same required variables to non-secret `var` entries and
`secret` entries:

```sh
dsec init examples/mcp/google_oidc_host.dsec -t dev
dsec run examples/mcp/google_oidc_host.dsec -t dev -- python examples/mcp/google_oidc_host.py
```

Configure the Google OAuth client with `https://<public-origin>/auth/callback`
as an authorized redirect URI. Point a tunnel or reverse proxy at
`http://127.0.0.1:8081`, and keep `MATLOCK_MCP_BASE_URL` set to the HTTPS
origin users access. Then start the host:

```sh
python examples/mcp/google_oidc_host.py
```

Use `--database /absolute/path/to/index.sqlite3` to select another existing
index. Add `--enable-semantic` only when its embedding provenance is verified.

The host authorizes only verified Google accounts in the configured allowlist.
It registers an `ExampleSearchPolicy`: requests are capped at 25 results, and
documents or chunks with the `mcp_example_hidden: true` metadata or attribute
marker are hidden. Replace that policy with trusted application hooks as
needed. Search policies do not receive authenticated user context and are not
a substitute for authentication or per-user authorization. This remains an
example deployment, not a supported Matlock service; review the proxy, tunnel,
Google OAuth configuration, allowed accounts, host permissions, and index
visibility before exposing it.
