# Matlock 2.0.0

Matlock is a modular Python application for indexing Markdown into SQLite,
auditing indexed content, and providing composable search tools.

Matlock is licensed under the [Apache License 2.0](LICENSE).

## Install

Python 3.11 or newer is required.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Audit and MCP dependencies are optional. Install `.[audit]` for heuristic
auditing, `.[audit,audit-llm]` to add the local LLM layer, and `.[mcp]` for
the MCP search integration. The embedding model is downloaded on first use.

## Configure and run

Copy `matlock.toml.example` to `matlock.toml` and update its `[ingest]` paths.
Ingestion settings live under `[ingest]`; optional audit settings live under
`[audit]`.

```sh
matlock --config matlock.toml check-config
matlock --config matlock.toml ingest
matlock --config matlock.toml watch
matlock --config matlock.toml audit check-config
matlock --config matlock.toml audit scan
```

The same CLI is available as `python -m matlock`. The default config is
`./matlock.toml`; the default database location is `.matlock/index.sqlite3`.
Old configuration and state are not migrated automatically.

## MCP integration

Matlock supplies importable search tools for registration on an existing,
host-owned FastMCP application, not a supported MCP service. Runnable stdio
and Google-authenticated HTTP hosts demonstrate local tool registration and
host configuration; see [MCP integration](docs/mcp.md).

## Tests and examples

```sh
python -m pip install -e '.[dev,mcp,audit]'
pytest
```

Tests use temporary databases and synthetic Markdown files. Set
`MATLOCK_TEST_VAULT` to a local test corpus to enable the optional integration
test.

- [Getting started](docs/getting-started.md)
- [Configuration and hooks](docs/configuration.md)
- [Database and troubleshooting](docs/operations.md)
- [Database structure](docs/database.md), [query examples](docs/queries.md),
  and [Python internals](docs/python-internals.md)
- [Shared SQLite index contract](docs/index-contract.md)
- [Complete ingestion example](docs/example.md), with a fictional vault in
  [`examples/second-brain/`](examples/second-brain/)
- [Secret-audit guide](docs/secret-scanner.md) and
  [example](docs/secret-scanner-example.md), with a vault in
  [`examples/secret-scanner/`](examples/secret-scanner/)
- [MCP integration](docs/mcp.md)
- [Changelog](docs/CHANGELOG.md)
