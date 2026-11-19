# Configuration and hooks

Pass `--config` before the command. If omitted, Matlock reads `matlock.toml`
from the current directory. Relative paths in the TOML file are resolved
relative to its directory. `matlock check-config` validates ingest settings
without loading the embedding model; `matlock audit check-config` validates
the audit settings.

## Ingest settings

| Section | Key | Default | Description |
| --- | --- | --- | --- |
| `[ingest]` | `vault_path` | `.` | Existing directory to ingest. |
| `[ingest]` | `database_path` | `.matlock/index.sqlite3` | SQLite database path. |
| `[ingest]` | `busy_timeout_ms` | `5000` | SQLite lock wait in milliseconds; positive integer. |
| `[ingest]` | `reconcile_interval_seconds` | `300` | Time between watcher reconciliation scans; positive integer. |
| `[ingest]` | `retry_attempts` | `3` | Maximum attempts for a watcher file event; at least one. |
| `[ingest]` | `retry_base_seconds` | `1.0` | Initial retry delay; nonnegative number. |
| `[ingest.chunking]` | `target_tokens` | `400` | Token budget per chunk, including heading context. |
| `[ingest.chunking]` | `overlap_tokens` | `50` | Overlap when splitting oversized text; nonnegative and less than the target. |
| `[ingest.embedding]` | `model` | `BAAI/bge-small-en-v1.5` | FastEmbed model name, also used to load a matching tokenizer. |
| `[ingest.embedding]` | `dimensions` | `384` | Number of values expected in each embedding. |
| `[ingest.embedding]` | `max_input_tokens` | `512` | Configured chunk input limit; must be at least the target. |
| `[ingest.hooks]` | `configured` | `[]` | Ordered array of importable `module:function` names. |

Use settings appropriate to the selected model; changing the model name alone
does not update `dimensions` or `max_input_tokens`. The database rejects
incompatible vector dimensions. Changes to chunk settings, hooks, or model
configuration cause existing notes to be reprocessed on the next ingest.

## Ingest metadata hooks

YAML frontmatter is loaded into a metadata dictionary. Configured hooks run in
order after parsing and receive a `DocumentContext` and the mutable dictionary.
The context exposes `relative_path` and `body`; the hook mutates metadata in
place and returns `None`. Values must remain JSON-serializable. Hooks are
trusted Python code, not sandboxed plugins.

The built-in hook can be enabled as follows:

```toml
[ingest.hooks]
configured = ["matlock_ingest.hooks:project_id_from_path"]
```

For `projects/orion/decision.md`, it sets `project_id` to `orion` if the
frontmatter did not already specify it, even as null. It does not write to the
source file. For another example hook, see the
[fictional vault example](example.md).

To supply a hook, place a module next to the config file (its directory is added to
the Python path) or anywhere else importable, and configure
`your_module:your_function`. For example:

```python
from matlock_ingest.models import DocumentContext


def enrich(context: DocumentContext, metadata: dict[str, object]) -> None:
    if context.relative_path.startswith("journal/"):
        metadata.setdefault("category", "journal")
```

If a hook raises an exception or leaves invalid metadata, that note fails
ingestion. Hook and embedding error details are redacted in the recorded error
message. See [operations](operations.md).

## Audit settings

The optional `[audit]` table configures `matlock audit` to read the shared
index and store scan annotations. It uses `[ingest]`'s database path and busy
timeout. Audit configuration, including LLM settings and audit hooks, is
described in the [secret-audit guide](secret-scanner.md). Install `.[audit]`
for heuristic scanning; add `.[audit-llm]` for the local LLM layer.

MCP host configuration remains the responsibility of the embedding host.
Matlock's importable MCP tools do not create or configure a server.

## Existing configuration and state

Matlock uses `matlock.toml` and `.matlock/` by default. It does not discover,
move, or migrate an older config or database automatically. Before reusing an
older configuration, manually move its settings into `[ingest]` (including
the nested chunking, embedding, and hook tables) and rename `[secret_scanner]`
to `[audit]`. Keep a backup of any config and database before changing paths.
