# Getting started

## Install

From the repository root, the directory containing `pyproject.toml`:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Python 3.11 or later is required. The first run that initializes the configured
embedding model may download model files. Embedding inference runs locally.
SQLite must support loading the required `sqlite-vec` extension.

Install optional features as needed:

```sh
python -m pip install -e '.[audit]'
python -m pip install -e '.[audit,audit-llm]'
python -m pip install -e '.[mcp]'
```

The `audit` extra installs the heuristic layer. Add `audit-llm` for the
transformers and torch dependencies used by the local LLM layer.

## Configure and run

From the repository root:

```sh
cp matlock.toml.example matlock.toml
```

Edit `[ingest]` in `matlock.toml` to set `vault_path` to an existing directory
and `database_path` to the desired SQLite file. Chunking, embedding, and ingest
hooks are nested under `[ingest.chunking]`, `[ingest.embedding]`, and
`[ingest.hooks]`. Optional audit settings use `[audit]`.

```sh
matlock --config matlock.toml check-config
matlock --config matlock.toml ingest
matlock --config matlock.toml watch
```

The `matlock` command also works as `python -m matlock`. `ingest` performs one
pass; `watch` repeats ingestion until interrupted. `--config` goes before the
command. Paths in the TOML file are resolved relative to that file.

The default config and database paths are `matlock.toml` and
`.matlock/index.sqlite3`. Existing configuration and `.matlock2/` state are
left untouched; Matlock does not migrate or move them automatically.

For a self-contained ingestion walkthrough, see the
[fictional second-brain example](example.md). To audit indexed content, see the
[audit guide](secret-scanner.md).
