# Changelog

Notable project changes are recorded here. Entries under **Unreleased** have
not necessarily been published as a release.

## Unreleased

### Changed

- Rebranded the distribution and application as Matlock 2.0.0, with one
  `matlock` command and `python -m matlock` entrypoint.
- Consolidated ingestion and audit under `matlock ingest|watch` and
  `matlock audit`; removed the old command, module, and hook compatibility
  shims.
- Moved the default configuration and database locations to `matlock.toml`
  and `.matlock/`; existing files and databases are not migrated automatically.
- Renamed the import packages to `matlock_ingest`, `matlock_audit`,
  `matlock_mcp`, and `matlock_index`.
- Constrained MCP support to optional host-composable tools and policies, with
  one minimal local stdio example and no Matlock-operated HTTP host.

### Fixed

- Ingest hook modules placed next to the config file are now importable, matching audit hooks.

### Added

- Shared `[ingest]` and `[audit]` configuration sections.
- Optional `audit` and `audit-llm` extras, keeping audit and local-LLM
  dependencies out of ingestion-only use.
- A shared `matlock_index` SQLite schema and connection contract for ingestion,
  audit, and read-only MCP consumers.
- A local two-layer (detect-secrets and local LLM) content audit that records
  findings in `content_attributes`, with a configurable prompt, size limits,
  and six hooks. The default LLM is the ungated
  `Qwen/Qwen2.5-1.5B-Instruct`.
- `SecretScanPolicy`, an MCP search policy that hides content the secret scanner flagged, left unscanned, or skipped as oversize.
- `matlock audit reset` removes stored audit results so content is scanned again.
- `scan-file --fail-above N` exits with status 1 when the risk score is above N.
- A GitHub Actions workflow that runs the test suite.
- `matlock audit scan-file` scans one file or standard input and prints the
  result as JSON.
- A secret scanner example that uses every scanner hook and a custom prompt.
- Content-addressed document bodies, chunk hashes, and a JSON attribute store.
- Optional MCP attribute filtering and host-provided query and visibility policies.
- Optional modular FastMCP search tools and a minimal embedded example host.
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
