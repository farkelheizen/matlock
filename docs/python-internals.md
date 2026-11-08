# Python internals

These are implementation details of the current ingestion package, not a
stable extension API except where a hook contract is documented.

## Data structures

All records below are defined in `src/matlock2/models.py`. `frozen=True`
prevents field reassignment but does not deep-freeze mutable values.

| Type | Fields and role |
| --- | --- |
| `IngestConfig` | Vault and database `Path`s, SQLite timeout, watcher reconciliation/retry settings, chunk budgets, embedding model/dimension/limit, and ordered hook names. `config.load_config` resolves paths relative to the TOML file and validates settings. |
| `DocumentContext` | `relative_path` and Markdown `body`, supplied to hooks. Paths use forward slashes relative to the vault. |
| `ParsedDocument` | `relative_path`, parsed `body`, and mutable `metadata` dictionary. Metadata is shared with the hook pipeline. |
| `Chunk` | `chunk_id`, zero-based `ordinal`, and `content` including any heading breadcrumb. The source path is associated by the persistence layer, not stored in the object. |
| `IngestFailure` | `relative_path`, `source_hash` (empty if reading failed), `stage`, and sanitized `error`. |
| `ScanReport` | Counts `discovered`, `ingested`, `skipped`, `removed`, a tuple of current `failures`, `unresolved_errors`, and `discovery_complete`. |
| `Tokenizer` | Protocol exposing `count(text) -> int`. |
| `Embedder` | Protocol exposing `embed(texts) -> list[list[float]]`, in input order. |

The hook contract is `hook(context, metadata) -> None`; it mutates the
JSON-serializable metadata dictionary in place. Hooks are trusted code. See
[configuration](configuration.md) for a runnable example.

## Control and persistence

- `cli` parses commands, loads config, constructs the database and model, and
  reports scan progress; `check-config` does not instantiate the model.
- `markdown` parses frontmatter into a `ParsedDocument`; `chunking` groups text
  by headings and produces ordered `Chunk` objects.
- `embedding.FastEmbedder` provides both model inference and token counting.
- `ingest.IngestionEngine` loads hooks, builds a processing fingerprint,
  coordinates reads, parsing, hooks, chunking, vectors, and persistence.
  `ingest_path` returns `"ingested"`, `"skipped"`, or `IngestFailure`.
- `database.Database` owns the connection, schema, transactions and FTS/vector
  synchronization. See [database structure](database.md).
- `watcher.VaultWatcher` queues filesystem events, debounces paths and invokes
  the same engine; periodic scans reconcile missed events.

The ingestion engine is responsible for closing its database through the CLI;
independent callers using `Database` should use its context manager or close
it explicitly.
