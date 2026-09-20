# Task: Implement `@matlock /documents` Slash Command (Matlock 0.9.0)

## Objective

Implement a deterministic slash command handler for `@matlock /documents` in the VS Code chat participant (`vscode.chat.createChatParticipant`).

Do **not** call the Copilot LLM (`vscode.lm`) for query execution or argument parsing. Parse arguments deterministically from `request.prompt`, execute the Matlock CLI via subprocess, and render the structured JSON response directly into `response.markdown(...)`.

The handler should invoke `matlock documents query`, which returns a JSON-only paginated document-query envelope.

---

## 1. Document Query Behavior

`matlock documents query` supports two result levels:

- **File granularity** (default): one result per matching document.
- **Chunk granularity**: indexed chunk hits with optional surrounding chunks.

When `--text` is absent or blank, the CLI falls back to metadata-only browsing, even if another search mode is supplied. By default, only active, non-generated documents are returned.

The command is JSON-only on stdout. Human-readable rendering belongs entirely to this extension handler.

The deprecated `matlock search query` command should not be used for new integrations. Its legacy `--stdio` transport remains separate and is not replaced by this handler.

---

## 2. CLI Command Specification

```bash
matlock documents query [OPTIONS]
```

| Flag | Type / Syntax | Repeatable | Description |
| :--- | :--- | :---: | :--- |
| `--text` | `string` | No | Optional document search text. Enables FTS, vector, or hybrid retrieval when nonblank. |
| `--search-mode` | `hybrid \| fts_only \| vector_only \| metadata_only` | No | Retrieval mode. Defaults to `hybrid`; no text normalizes execution to metadata-only. |
| `--min-score` | `float` | No | Minimum score for text-search matches. |
| `--project-id` | `string` | Yes | Case-insensitive exact project ID match. Values OR within the option. |
| `--super-project-id` | `string` | Yes | Case-insensitive exact super-project ID match. Values OR within the option. |
| `--file-path` | `string` | Yes | Prefix of the normalized vault-relative path. Leading `/` characters are stripped. Values OR within the option. |
| `--file-ext` | `string` | Yes | Exact file-extension match, such as `.md`. Values OR within the option. |
| `--created` | `[=, <, <=, >, >=]YYYY-MM-DD` | Yes | Created-date predicate. Repeated predicates AND-compose. |
| `--modified` | `[=, <, <=, >, >=]YYYY-MM-DD` | Yes | Modified-date predicate. Repeated predicates AND-compose. |
| `--attributes` | `PATH:OPERATOR:VALUE` | Yes | Frontmatter JSON-path filter. Repeated filters AND-compose. |
| `--granularity` | `file \| chunk` | No | Result level. Defaults to `file`. |
| `--surrounding-chunks` | `integer` (`0-3`) | No | Adjacent chunks before and after a chunk hit. |
| `--limit` | `integer` ($\ge 1$) | No | Page size; defaults to configured `queries.default_limit`. |
| `--offset` | `integer` ($\ge 0$) | No | Number of matching results to skip. |
| `--count-only` | flag | No | Return pagination statistics without result rows. |

Filter composition:

- Repeated values within one option OR together, except repeated `--created`, `--modified`, and `--attributes`, which AND-compose.
- Different filter categories AND-compose.
- `--file-path` uses normalized vault-relative prefixes, not filesystem absolute paths or wildcard patterns.

### Frontmatter Attribute Filter (`--attributes`)

- Format: `PATH:OPERATOR:VALUE`.
- Bare paths are prefixed with `$.`; for example, `status:eq:active` becomes `$.status`.
- Operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`.
- Values that look like integers, floats, booleans, or `null` are coerced to those JSON types.
- `in` accepts comma-separated values, for example `tags:in:ops,platform`.

Examples:

```bash
poetry run matlock documents query
poetry run matlock documents query --text "database" --search-mode fts_only
poetry run matlock documents query --file-path /Notes/ --file-ext .md
poetry run matlock documents query --attributes "status:eq:active"
poetry run matlock documents query --project-id Backend --project-id platform
poetry run matlock documents query --modified ">=2026-01-01" --modified "<2026-02-01"
poetry run matlock documents query --granularity chunk --surrounding-chunks 1
poetry run matlock documents query --count-only
```

File granularity returns metadata only with `file_details.content` set to `null`. Chunk granularity implicitly returns matched and requested surrounding content through the CLI's secret-safe redaction path.

---

## 3. TypeScript Contracts

```typescript
export interface QueryResponseStats {
  total_matches: number;
  returned_matches: number;
  limit: number | null;
  offset: number;
}

export interface DocumentScoreBreakdown {
  fts_rank: number | null;
  vector_similarity: number | null;
  rrf_score: number | null;
}

export interface DocumentChunkDetails {
  chunk_id: string;
  chunk_index: number;
  total_chunks: number;
  content: string | null;
  before: string[];
  after: string[];
}

export interface DocumentFileDetails {
  total_matching_chunks: number;
  content: string | null;
}

export interface DocumentRecord {
  file_path: string;
  absolute_path: string;
  file_ext: string | null;
  created: string;
  modified: string;
  modified_date: string | null;
  length: number | null;
  word_count: number | null;
  project_ids: string[];
  project_id: string | null;
  super_project_id: string | null;
  score: number;
  score_breakdown: DocumentScoreBreakdown;
  frontmatter: Record<string, unknown>;
  has_secrets: boolean | null;
  secret_detection_error: string | null;
  chunk_details: DocumentChunkDetails | null;
  file_details: DocumentFileDetails | null;
}

export interface DocumentQueryResponse extends QueryResponseStats {
  results: DocumentRecord[];
}
```

Implementation should tolerate nullable metadata fields and preserve the invariant that a result contains either `chunk_details` or `file_details`, never both.

---

## 4. Implementation Guidelines

### 1. Argument Parser (`src/queryParser.ts`)

Parse `request.prompt` deterministically without `vscode.lm`. Suggested forms:

- `text:"database migration"` -> `--text "database migration"`
- `mode:fts` -> `--search-mode fts_only`
- `mode:vector` -> `--search-mode vector_only`
- `mode:hybrid` -> `--search-mode hybrid`
- `mode:metadata` -> `--search-mode metadata_only`
- `project:backend` -> `--project-id backend`
- `super-project:operations` -> `--super-project-id operations`
- `path:/Notes/` -> `--file-path /Notes/`
- `ext:.md` -> `--file-ext .md`
- `created:>=2026-01-01` -> `--created ">=2026-01-01"`
- `modified:<2026-02-01` -> `--modified "<2026-02-01"`
- `attr:status:eq:active` -> `--attributes "status:eq:active"`
- `granularity:chunk` -> `--granularity chunk`
- `around:1` -> `--surrounding-chunks 1`
- `score:0.4` -> `--min-score 0.4`
- `limit:10` / `offset:20` -> `--limit 10 --offset 20`
- `count-only` -> `--count-only`
- `no-content` -> `--no-include-content`

Recommended parser rules:

- Preserve quoted values containing spaces.
- Allow repeated keys for repeatable options.
- Treat unrecognized `key:value` tokens as an input error rather than guessing.
- Return actionable validation messages before invoking the subprocess.
- Never interpolate raw prompt text into a shell command. Pass arguments as an argv array.

### 2. Execution and Response Flow

1. Execute `matlock documents query [args] --config <path>` with a subprocess API that accepts an argv array.
2. Capture stdout, stderr, and the numeric exit code.
3. On a non-zero exit code, render stderr as an error message without attempting to parse partial stdout.
4. Parse stdout as `DocumentQueryResponse` using a runtime validator such as Zod or an equivalent schema library.
5. Render the validated response through `response.markdown(...)`.
6. Keep raw document content out of logs and diagnostic messages.

### 3. Rendering

Summary for normal results:

```text
Found **${total_matches}** documents (showing ${offset + 1}-${offset + returned_matches}).
```

For `--count-only`:

```text
Found **${total_matches}** documents.
```

File-level results should render:

- Document path as the primary link/text.
- Score and project/super-project context when present.
- Modified date and extension when present.
- Frontmatter metadata in a compact details section when useful.
- Do not render file content; file-level `file_details.content` is always `null`.

Chunk-level results should render:

- Document path and chunk identifier.
- Score and score breakdown when available.
- Matched chunk content.
- Surrounding `before` and `after` content when present.

Never render `secret_detection_error` as document content. If `has_secrets` is true, trust the CLI's redacted content and do not attempt to read the source file directly.

### 4. Pagination

If `offset + returned_matches < total_matches`, render a next-page action such as:

```text
[Next page](command:matlock.documentsQuery?${encodedQuery})
```

The action must preserve the original query and increase `offset` by `limit`. Do not render a next-page action for count-only responses or when the current page is complete.

### 5. Error Handling and Safety

- Input errors: show the parser or CLI validation message and preserve the non-zero result.
- Database/search errors: show the CLI error without fabricating partial results.
- Embedding-provider errors: tell the user that the selected vector/hybrid search could not run and suggest `mode:fts` only when that fallback is explicitly appropriate.
- Treat all document content as untrusted Markdown. Escape or structure generated links safely and avoid constructing links from unvalidated absolute paths.
- Do not call `vscode.lm` for parsing, searching, ranking, or summarization.

---

## 5. Suggested Test Coverage

- Parses quoted text and repeated `project`, `super-project`, `path`, and `ext` keys.
- Parses date comparisons containing `<` and `>` without shell interpretation.
- Parses frontmatter attributes and preserves typed values.
- Rejects unknown keys, invalid modes, invalid granularity, invalid score, invalid limits, and invalid surrounding-chunk counts.
- Builds argv arrays without shell interpolation.
- Correctly renders file-level and chunk-level responses.
- Renders count-only responses without result rows or pagination actions.
- Renders next-page actions with preserved filters and updated offset.
- Handles non-zero CLI exit codes and malformed JSON responses.
- Does not expose raw secret content or scanner errors.
- Keeps legacy `search query --stdio` behavior outside this new handler.
