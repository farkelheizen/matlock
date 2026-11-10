# Example: ingest a fictional second brain

This example uses only the small synthetic vault checked into
[`examples/second-brain/vault/`](../examples/second-brain/vault/). It is
independent of your own notes and of the larger optional test corpus. The
example configuration and generated database live under
`examples/second-brain/`; `.sqlite3` database files are ignored by Git.

## 1. Set up

Open a terminal in the repository root and install Matlock2 as described in
[getting started](getting-started.md). Then activate the repository's virtual
environment:

```sh
cd /path/to/matlock2
. .venv/bin/activate
export PYTHONPATH="$PWD/examples/second-brain${PYTHONPATH:+:$PYTHONPATH}"
```

The example's [`matlock2.toml`](../examples/second-brain/matlock2.toml) uses
paths relative to its own directory. The `PYTHONPATH` setting makes the
example's [`project_metadata.py`](../examples/second-brain/project_metadata.py)
hook importable for the scan and watch commands below. The example uses the
default embedding model. The first scan may download the model.

## 2. Scan

```sh
matlock-ingest --config examples/second-brain/matlock2.toml check-config
matlock-ingest --config examples/second-brain/matlock2.toml ingest
```

On a fresh example database the scan should report five discovered and five
ingested notes, with zero unresolved errors. A subsequent scan with unchanged
inputs reports five skipped notes.

The hook matches direct Markdown files at `projects/<project_id>/<file>.md`.
It sets missing or YAML-null `project_id` values to the path's project ID,
while preserving existing non-null values (including an empty string). It
mutates only indexed metadata, not source Markdown. The example database is
`examples/second-brain/.matlock2/index.sqlite3`.

## 3. Inspect the database

From the repository root, query the synthetic data with Python:

```sh
python - <<'PY'
import sqlite3

path = "examples/second-brain/.matlock2/index.sqlite3"
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
    print("Documents:")
    for row in db.execute(
        "SELECT path, json_extract(metadata_json, '$.project_id') "
        "FROM source_documents ORDER BY path"
    ):
        print(row)

    print("\nFTS matches for 'migration':")
    for row in db.execute(
        "SELECT c.source_path, c.content FROM chunks_fts "
        "JOIN chunks AS c ON c.id = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ? ORDER BY c.source_path",
        ("migration",),
    ):
        print(row)

    print("\nUnresolved errors:")
    for row in db.execute(
        "SELECT source_path, stage, error_details FROM ingestion_errors "
        "WHERE status = 'unresolved'"
    ):
        print(row)
PY
```

The document list contains five paths. The decision note's null value and the
overview note's missing value become `atlas`; the retrospective retains
`custom-atlas`. The FTS query returns the decision note containing
`migration`. The last section is empty after a successful scan. This query is
an inspection example, not a supported search API or MCP tool.
For metadata, full-text, and vector query examples, see
[querying the example database](queries.md).

## 4. Try watching

```sh
matlock-ingest --config examples/second-brain/matlock2.toml watch
```

The watcher runs an initial scan and waits for file events. Stop it with
Ctrl-C. To experiment with edits without changing tracked example files, copy
the example vault, configuration, and hook to a separate scratch directory,
preserve the `vault/` subdirectory layout, set `PYTHONPATH` to include that
directory, and point `--config` to its configuration.
