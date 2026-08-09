# Matlock Search

**Version:** 0.3.x

Matlock Search is an optional local-first retrieval subsystem layered on top of the core pipeline. It uses the same SQLite database as the rest of Matlock and adds chunk, FTS, and embedding-backed search state without changing the default behavior of non-search commands.

---

## Overview

Search adds two user-facing commands:

| Command | Purpose |
|:--------|:--------|
| `matlock search index` | Build or refresh local search state for active vault files |
| `matlock search query` | Query the local index in human mode or strict machine mode |

Related orchestration hooks:

- `matlock run-all --index-search` runs search indexing after `report`.
- `matlock server --index-search` runs one startup indexing pass.
- `matlock server --index-search-continuous` keeps polling for stale search work in a background thread.

Search remains opt-in. If you never call the search commands or flags, the core sync/parse/map-projects/rollup/report workflow behaves as before.

---

## Indexing Flow

`matlock search index` operates on active, non-generated Markdown files.

1. Select files whose search freshness is missing or stale (`search_indexed_at` / `search_index_hash`), plus any files forced by CLI `--force` or per-file override.
2. Read the file, parse frontmatter when available, and apply per-file search overrides.
3. Split the Markdown body into deterministic chunks.
4. Optionally prefix each chunk with rendered frontmatter and project context.
5. Replace the file's `search_chunks` rows, let FTS triggers update `search_fts`, and write embeddings into `search_vec`.
6. Mark the file as indexed and purge orphaned rows for deleted/generated files.

Current indexing behavior:

- Generated report files (`is_generated = 1`) are never indexed.
- Soft-deleted files are never indexed.
- Chunk IDs are stable within a file: `<file_path>#<zero-padded chunk_index>`.
- Batch commit cadence comes from `search.indexing.batch_size` and can be overridden per run with `--batch-size`.
- Embedding model name can be overridden per run with `--model`.

### Per-file frontmatter overrides

Matlock recognizes a namespaced `matlock.search` frontmatter block:

```yaml
matlock:
  search:
    exclude: true
    force_index: true
```

- `exclude: true` clears search rows for the file and suppresses chunk/vector writes.
- `force_index: true` makes the file eligible for re-indexing even when its stored search hash still matches.

---

## Query Modes

`matlock search query` supports four execution modes:

| Mode | Behavior |
|:-----|:---------|
| `metadata_only` | Apply filters only; no lexical or vector scoring required |
| `fts_only` | Use SQLite FTS over chunk content |
| `vector_only` | Use embedding similarity over stored vectors |
| `hybrid` | Combine lexical and vector ranking with reciprocal-rank fusion |

Output controls:

- `granularity: chunk` returns chunk-level hits with optional surrounding chunks.
- `granularity: file` collapses results to the file level.
- `include_content` controls whether matched text is returned.
- Empty or missing `query` values are normalized to `metadata_only`.

Project filters currently execute as exact matching even though the request contract accepts additional `project_match_mode` values for forward compatibility.

---

## Machine Transport (`--stdio`)

`matlock search query --stdio` is the machine-facing transport for editor and agent integrations.

- stdin: one JSON object matching `matlock.search.v1`
- stdout: one JSON object matching `matlock.search.response.v1`
- stdout purity: no human logging or other text is allowed
- stderr: reserved for error-level logging only

Deterministic exit codes:

| Code | Meaning |
|:-----|:--------|
| `0` | Success |
| `1` | Input or schema error |
| `2` | Database or search storage error |
| `3` | Embedding provider error |

Malformed or invalid requests still return a structured JSON error payload whenever the process can emit one.

Example:

```bash
printf '{"query": "database", "search_mode": "fts_only"}' | poetry run matlock search query --stdio
```

---

## Logging

Search query transport uses isolated logging so machine-mode stdout stays clean.

- When `log_path` is configured, search query logs are written to a sibling `search.log` file.
- When `log_path` is unset, search query logs go to `~/.matlock/logs/search.log`.
- During a query run, the isolated logging context routes DEBUG-and-above output to the search log file and ERROR-and-above output to stderr.

This isolation applies to query execution. The rest of the CLI and server continue using the normal Matlock logging configuration.

---

## Configuration

Search configuration lives under the top-level `search` block in `config.yaml`:

```yaml
search:
  indexing:
    enabled: false
    batch_size: 100

  chunking:
    strategy: fixed_token
    chunk_size: 500
    chunk_overlap: 50
    inject_frontmatter: true
    frontmatter_template: "[Project: {db.project_id} | File: {sys.file_name} | Status: {fm.status}]\n\n"

  embedding:
    provider: fastembed
    model_name: sentence-transformers/all-MiniLM-L6-v2
    dimensions: 384
    api_base_url: null
    api_base_url_env_var: null
    api_key_env_var: OPENAI_API_KEY
```

Notes:

- `search.indexing.enabled` is a stored config flag, but runtime execution is still explicitly opt-in through CLI commands and flags.
- `api_base_url`, `api_base_url_env_var`, and `api_key_env_var` are only used when `provider` is `openai-compatible`; they are ignored for `fastembed`.
- `api_base_url_env_var` and `api_key_env_var` opt into environment overrides only for declared keys.
- `chunk_overlap` must be smaller than `chunk_size`.

---

## Storage Summary

Search adds three SQLite structures and two freshness columns on `file`:

- `file.search_indexed_at`
- `file.search_index_hash`
- `search_chunks`
- `search_fts`
- `search_vec`

See `docs/matlock-data-model.md` for the full schema and trigger details.

---

## Related Docs

- `docs/matlock-cli.md` for full command syntax.
- `docs/matlock-configuration.md` for the complete `search` config schema.
- `docs/matlock-data-model.md` for request/response contracts and SQLite schema.
- `docs/matlock-pipeline-specification.md` for `run-all` and `server` integration behavior.
- `docs/matlock-high-level-design.md` for the broader architecture view.