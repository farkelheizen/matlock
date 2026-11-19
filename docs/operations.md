# Operation and troubleshooting

## What ingestion does

The engine walks the configured vault for `.md` files, reads their bytes and
stores a SHA-256 digest. It skips a previously indexed file when its digest and
processing fingerprint match. It processes changed files and removes indexed
records for files no longer found during a complete scan. Symbolic links are
not followed during discovery.

For each note, frontmatter becomes JSON metadata and the body is split into
heading-aware chunks. Each chunk is stored alongside an FTS5 index and a
`sqlite-vec` embedding. File replacement is transactional. If a changed note
fails, its previous successfully indexed representation remains in the
database until a later successful ingestion.

`ingest` reports per-file status and returns a nonzero exit code when
ingestion errors remain or discovery was incomplete.
`watch` starts with an ingest pass,
processes filesystem events, and periodically reconciles the vault. Event
retries use exponential delays capped at 30 seconds; later scans can retry
again.

## Inspect errors

The database contains note content and metadata: treat it as private. For a
database at `.matlock/index.sqlite3`, this read-only inspection uses Python's
standard SQLite module:

```sh
python - <<'PY'
import sqlite3

with sqlite3.connect("file:.matlock/index.sqlite3?mode=ro", uri=True) as db:
    for row in db.execute(
        "SELECT source_path, stage, attempt_count, status, error_details "
        "FROM ingestion_errors ORDER BY last_attempt_at DESC"
    ):
        print(row)
PY
```

Fix the file or configuration and ingest again. Error statuses are retained as
`unresolved`, `resolved`, or `superseded`; a failure to read a file can have an
empty hash because the bytes were not available.

## Common issues

- **`pip install -e .` says no project was found:** run it from the repository
  directory containing `pyproject.toml`, not from a notes directory.
- **Vault path is not a directory:** check the configured path. Relative paths
  are relative to the configuration file.
- **Model initialization fails:** check the FastEmbed model name and initial
  download connectivity. `check-config` does not test the model download.
- **`sqlite-vec` cannot load:** verify the installed extension and that the
  active Python's SQLite supports extension loading on your platform.
- **Configured dimension differs from the database:** use a new database or
  migrate the existing one before switching to a model with a different
  embedding dimension.

The application provides ingestion and audit commands. MCP search tools are
available as an optional embedding integration, not as a Matlock-operated
server. The [example](example.md) shows how to inspect indexed data directly.

## Running the secret scanner

After a scan, `matlock audit scan` (or `run`) evaluates content that has no
attribute row yet and records findings. It can run beside `watch`. See the
[secret scanner guide](secret-scanner.md) and its [example](secret-scanner-example.md).
