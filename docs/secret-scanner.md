# Secret scanner

`matlock2-scanner` is an internal sub-app that looks for secrets and private
data in content that `matlock2` has already indexed, and records its findings in
the [`content_attributes`](attributes.md) table. It is a separate process: it
reads the index database configured in `matlock2.toml` and never reads or
changes the vault.

The scanner only **records findings**. Nothing in the index or the MCP tools is
hidden, redacted, or deleted because of them; a reader that wants to act on
findings must query them (see [Reading findings](#reading-findings)).

## How it works

Two layers evaluate each unit of content:

1. **Heuristic layer.** [`detect-secrets`](https://github.com/Yelp/detect-secrets)
   plugins run on the in-memory text (AWS keys, private keys, tokens, basic-auth
   URLs, keyword assignments, high-entropy strings, and so on). Inline
   `pragma: allowlist secret` comments are ignored on purpose. Nothing is
   written to disk and no baseline file is used.
2. **LLM layer.** A small open-weights model (default
   `meta-llama/Llama-3.2-1B-Instruct`) is loaded into the scanner process with
   `transformers`; no daemon or network service is involved apart from the
   one-time model download (set `HF_HUB_OFFLINE=1` once the model is cached).
   It receives a configurable prompt and answers with a JSON object that is
   validated and clamped. The model is loaded once, on first use.

The units are **chunks** (keyed by `chunks.content_sha256`) and whole
**documents** (keyed by `source_documents.body_sha256`). Documents are scanned
directly; their result is stored against the document hash and is not
aggregated from chunk results. Identical text shares one result.

### Scan once per hash

An item is scanned only if `content_attributes` has **no row at all** for its
`(domain, sha256)`. If any row exists, even `{}` or one written by another
tool, the item is not scanned. Results are valid forever: changing the prompt,
model, rules, or hooks does not trigger a rescan. Edited content has a new hash,
so it is scanned. To re-evaluate content on purpose, delete its row (or only the
scanner key) in a write transaction.

The scanner only ever inserts rows (`INSERT ... ON CONFLICT DO NOTHING`), so it
cannot overwrite attributes from other writers and is safe to run next to
`matlock2 watch`. Ingestion does not delete rows for unchanged content.

### Size limits

`max_chunk_chars` and `max_document_chars` bound what is scanned. Longer content
goes to the [`on_oversize` hook](#hooks), which may return a shorter text to
scan (for example head and tail excerpts) or `None`. With no hook, or when the
hook returns `None`, the item is skipped and a row is still written
(`llm_status: "skipped"`, `skip_reason: "oversize"`, `risk_score: 0`) so it is
not retried every pass. Skipped content is therefore **not** evidence of safety.

## Install

```bash
pip install -e ".[scanner]"       # heuristic layer (detect-secrets, pydantic)
pip install -e ".[scanner-llm]"   # also transformers and torch for the LLM layer
```

The heuristic layer works without `torch`. Use `--no-llm` or
`enabled_layers = ["heuristic"]` to run without the LLM extras.

## Commands

```bash
matlock2 --config matlock2.toml scan            # index first; the scanner needs the database
matlock2-scanner --config matlock2.toml check-config
matlock2-scanner --config matlock2.toml scan    # one pass, then exit
matlock2-scanner --config matlock2.toml run     # loop every interval_seconds
```

`python -m matlock2_scanner` is equivalent. Options for `scan` and `run`
(also accepted by `check-config` to check a given mode):

| Option | Effect |
|---|---|
| `--no-llm` / `--no-heuristics` | Disable a layer for this run |
| `--limit N` | Scan at most N items per pass |
| `--dry-run` | Evaluate and report without writing or calling `post_scan` |
| `--max-passes N` (`run`) | Stop after N passes |

`run` stops cleanly on SIGINT or SIGTERM after finishing the current batch.
A pass prints `scanned`, `skipped`, `flagged` (heuristic match or risk of 5 or
more), and `errors`. Exit code 0 is success and 2 is a configuration, database,
or dependency error. Per-item failures do not fail a pass.

## Configuration

The scanner reuses `[ingest]` (`database_path`, `busy_timeout_ms`) and reads an
optional `[secret_scanner]` table. Unknown keys are rejected. Relative paths
follow the configuration file.

| Key | Default | Meaning |
|---|---|---|
| `enabled_layers` | `["heuristic", "llm"]` | Layers to run |
| `interval_seconds` | `60` | Pause between passes in `run` |
| `batch_size` | `50` | Items evaluated per write transaction |
| `domains` | `["chunk", "document"]` | Content levels to scan |
| `attribute_key` | `"secret_scan"` | Top-level key in `attributes_json` |
| `max_chunk_chars` | `4000` | Largest chunk scanned as-is |
| `max_document_chars` | `50000` | Largest document body scanned as-is |
| `[heuristics] plugins` | all defaults | `detect-secrets` plugin class names, such as `AWSKeyDetector` |
| `[llm] model_id` | `meta-llama/Llama-3.2-1B-Instruct` | Hugging Face model |
| `[llm] device` | `"auto"` | `auto`, `cpu`, `cuda`, or `mps` |
| `[llm] max_new_tokens` | `256` | Generation limit |
| `[llm] prompt_file` | built-in | Prompt template path |
| `[llm] prompt` | built-in | Inline prompt (not with `prompt_file`) |
| `[hooks]` | none | One `module:function` per hook slot |

### The prompt

The template must contain the `{content}` placeholder, which is replaced with
the text under review. Only that exact placeholder is substituted, so other
braces (such as a JSON example) are safe. The model should reply with one JSON
object:

```json
{"risk_score": 0, "regex_matches_found": false, "llm_confidence_score": 0,
 "reason": "short explanation", "flagged_snippets": []}
```

Scores are clamped to 0-10. `regex_matches_found` is always taken from the
heuristic layer, never from the model. See the
[built-in prompt](../src/matlock2_scanner/models.py) and the
[example prompt](../examples/secret-scanner/prompt.txt).

## Stored result

Written under `attributes_json -> secret_scan` (or your `attribute_key`):

```json
{
  "risk_score": 8,
  "regex_matches_found": true,
  "llm_confidence_score": 2,
  "reason": "short explanation",
  "flagged_snippets": ["ab***"],
  "heuristic_findings": [
    {"type": "AWS Access Key", "line": 3, "preview": "AK***", "source": "detect-secrets"}
  ],
  "layers": ["heuristic", "llm"],
  "llm_status": "ok",
  "provenance": {"scanner_version": "0.1.0", "model_id": "...", "prompt_sha256": "..."},
  "scanned_at": "2026-01-01T00:00:00.000000Z"
}
```

- `llm_status` is `ok`, `disabled` (layer off), `skipped` (see `skip_reason`:
  `pre_scan` or `oversize`), `parse_error`, or `error`.
- Default `risk_score` is the larger of the LLM score and `8` when the
  heuristic layer matched; the `risk_policy` hook may replace it (0-10).
- Unparseable model output keeps the heuristic result, with a fallback LLM risk
  of 5 if the heuristics matched and 0 otherwise. A failure while generating,
  or in a hook, stores `risk_score: 5` and an `error` that names only the stage
  and exception type. These fail closed, but are stored once like any result.
- `provenance` is informational and is never used to decide on rescans.

Findings are never stored as plaintext secrets: previews and snippets keep at
most two leading characters. The model's `reason` is truncated to 500
characters but is free text, so treat the database as sensitive. Per-item logs
contain only stage and exception names, never content.

## Hooks

Each slot takes exactly one `module:function` under `[secret_scanner.hooks]`.
Modules next to the configuration file are importable. Signatures are checked at
load, so `check-config` finds mistakes. A hook that raises (or returns a bad
value) makes that item an `error` result, except `post_scan`, whose failures
are logged and ignored. A `ScanItem` has `domain`, `sha256`, `content`,
`source_paths`, `chunk_ordinals`, and `document_metadata` (ingest metadata keyed
by path).

| Slot | Signature | Purpose |
|---|---|---|
| `pre_scan` | `(ScanItem) -> bool` | Return `False` to skip the item (`skip_reason: "pre_scan"`) |
| `heuristic_rules` | `(ScanItem) -> list[Finding]` | Extra findings beyond `detect-secrets`; runs only if the heuristic layer is on. Previews are masked by the scanner |
| `prompt_context` | `(ScanItem, template) -> str` | Adjust the prompt template for one item; must keep `{content}` |
| `risk_policy` | `(ScanItem, ScanResult) -> int` | Final `risk_score` (clamped to 0-10) |
| `on_oversize` | `(ScanItem, limit) -> str \| None` | Text to scan instead of over-limit content, or `None` to skip |
| `post_scan` | `(ScanItem, ScanResult) -> None` | Side effects after the batch is committed (reports, alerts); not called on `--dry-run` |

`Finding(type, line=0, preview="", source="rule")` and `ScanResult` are frozen
dataclasses in `matlock2_scanner.models`. The existing ingest metadata hook
(`[hooks] configured`) is separate; it can label documents so scanner hooks can
use `document_metadata`. The [example](secret-scanner-example.md) uses all of
them.

## Reading findings

```sql
-- Chunks at or above a risk threshold, with their paths
SELECT c.source_path, c.ordinal,
       json_extract(a.attributes_json, '$.secret_scan.risk_score') AS risk
FROM content_attributes a
JOIN chunks c ON a.domain = 'chunk' AND c.content_sha256 = a.sha256
WHERE json_extract(a.attributes_json, '$.secret_scan.risk_score') >= 5
ORDER BY risk DESC;
```

The optional MCP attribute filtering in [mcp.md](mcp.md) can use the same
fields. Content without a row has not been scanned, and a row with `skipped` or
`error` status was not fully evaluated.

## Operating notes

- Run `matlock2 scan` or `watch` first; the scanner reports an error if the
  index database is missing or predates content attributes.
- Run `matlock2-scanner run` beside `matlock2 watch`. Writes are short
  `BEGIN IMMEDIATE` transactions that honor `busy_timeout_ms`.
- A model that cannot be loaded aborts the pass with exit code 2 and nothing is
  stored, so fix the cause and rerun.
- The heuristic layer uses a private `detect-secrets` helper so it can scan
  text without files; the dependency is therefore pinned below version 2.
- Heuristics and a small model miss things and raise false alarms. They are a
  safety net, not proof that content is free of secrets.
