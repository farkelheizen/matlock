# Changelog

Notable project changes are recorded here. Entries under **Unreleased** have
not necessarily been published as a release.

## Unreleased

### Added

- `matlock2-scanner`: a local two-layer (detect-secrets and local LLM) secret scanner that records
  findings in `content_attributes`, with a configurable prompt, size limits, and six hooks.
- `matlock2-scanner scan-file` scans one file or standard input and prints the result as JSON.
- A secret scanner example that uses every scanner hook and a custom prompt.
- Content-addressed document bodies, chunk hashes, and a JSON attribute store.
- Optional MCP attribute filtering and host-provided query and visibility policies.
- Opt-in sanitized HTTP routing diagnostics for the Google MCP example.
- Optional semantic, vector, and hybrid search in the Google-authenticated MCP example.
- Google-authenticated HTTP MCP example with an explicit email allowlist.
- Optional modular FastMCP search tools and a separate example stdio host.
- Database schema, Python internals, and runnable metadata, FTS, and vector
  query examples.
- Expanded setup, configuration, operations, and example documentation.
- A small fictional second-brain example for a self-contained ingestion demo.
- An example hook that fills missing or null project identifiers from file paths.

## 0.1.0

### Added

- Python CLI for one-shot Markdown ingestion and filesystem watching.
- SQLite persistence with JSON metadata, FTS5, and `sqlite-vec` embeddings.
- YAML frontmatter parsing, metadata hooks, and configurable chunking.
- File hashing, incremental ingestion, reconciliation, and persistent errors.
