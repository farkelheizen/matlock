# MCP integration

Matlock provides optional, importable search tools for a host-owned MCP
application. It does not ship or operate a Matlock MCP server, server command,
authentication service, or supported transport lifecycle. The embedding host
owns server creation, transport, authentication and authorization, logging,
configuration, and lifecycle. The only runnable host is a minimal local
example for demonstrating tool registration.

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

The one example creates a tiny FastMCP application only to demonstrate
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
