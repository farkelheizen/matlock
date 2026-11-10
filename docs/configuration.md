# Configuration and hooks

Pass `--config` before the command. If omitted, Matlock2 looks for
`matlock2.toml` in the current directory. Relative paths in the TOML file are
resolved relative to the file's directory, not the shell's working directory.
`check-config` validates settings and paths; it does not load the embedding
model or run an ingestion.

## Settings

| Section | Key | Default | Description |
| --- | --- | --- | --- |
| `ingest` | `vault_path` | `.` | Existing directory to scan. |
| `ingest` | `database_path` | `.matlock2/index.sqlite3` | SQLite database path. |
| `ingest` | `busy_timeout_ms` | `5000` | SQLite lock wait in milliseconds; positive integer. |
| `ingest` | `reconcile_interval_seconds` | `300` | Time between watcher reconciliation scans; positive integer. |
| `ingest` | `retry_attempts` | `3` | Maximum attempts for a watcher file event; at least one. |
| `ingest` | `retry_base_seconds` | `1.0` | Initial retry delay; nonnegative number. |
| `chunking` | `target_tokens` | `400` | Token budget per chunk, including heading context. |
| `chunking` | `overlap_tokens` | `50` | Overlap when splitting oversized text; nonnegative and less than the target. |
| `embedding` | `model` | `BAAI/bge-small-en-v1.5` | FastEmbed model name, also used to load a matching tokenizer. |
| `embedding` | `dimensions` | `384` | Number of values expected in each embedding. |
| `embedding` | `max_input_tokens` | `512` | Configured chunk input limit; must be at least the target. |
| `hooks` | `configured` | `[]` | Ordered array of importable `module:function` names. |

Use the settings appropriate to the selected model; changing the model name
alone does not automatically update `dimensions` or `max_input_tokens`. The
database rejects a change in vector dimensions: use a new database or migrate
the existing one. Changes to chunk settings, hooks or model configuration
cause existing notes to be reprocessed on the next ingest.

## Metadata hooks

YAML frontmatter is loaded into a metadata dictionary. Configured hooks run in
order after parsing and receive a `DocumentContext` and the mutable dictionary.
The context exposes `relative_path` and `body`; the hook mutates metadata in
place and returns `None`. Values must remain JSON-serializable. Hooks are
trusted Python code, not sandboxed plugins.

The built-in hook can be enabled as follows:

```toml
[hooks]
configured = ["matlock2_ingest.hooks:project_id_from_path"]
```

Existing configuration using `matlock2.hooks:project_id_from_path` continues
to work through a compatibility shim; use the `matlock2_ingest` path for new
configurations.

For `projects/orion/decision.md`, it sets `project_id` to `orion` if the
frontmatter did not already specify it, even as null. It does not write to the
source file. For a separate example hook that replaces null values, see the
[fictional vault example](example.md).

To supply your own, place an importable module on the Python import path and
configure `your_module:your_function`. For example:

```python
from matlock2_ingest.models import DocumentContext


def enrich(context: DocumentContext, metadata: dict[str, object]) -> None:
    if context.relative_path.startswith("journal/"):
        metadata.setdefault("category", "journal")
```

If a hook raises an exception or leaves invalid metadata, that note fails
ingestion. Hook and embedding error details are intentionally redacted in the
recorded error message. See [operations](operations.md).

## Secret scanner settings

The optional `[secret_scanner]` table configures `matlock2-scanner`, which reads
the same `[ingest]` settings (database path and busy timeout). Its keys, prompt
template, and the six scanner hook slots are described in the
[secret scanner guide](secret-scanner.md). These are separate from the ingest
metadata hooks above, and the ingestion command ignores the table.
The shared SQLite schema and connection boundary is documented in the
[index contract](index-contract.md).
