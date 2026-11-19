# Search & Document Query Use Cases

**Version:** 1.0.x

This is a scenario-driven companion to `docs/matlock-search.md` and
`docs/matlock-cli.md`. Each entry starts with a plain-English question a
vault user might ask, followed by the exact `matlock` command(s) that pull
the data needed to answer it.

All examples assume a config like:

```bash
export MATLOCK_CFG=/path-to/fake-second-brain/.matlock/config/matlock.yaml
```

and are shown as `matlock --config "$MATLOCK_CFG" ...`. Project IDs
(`prj-26-07`, `mnt-26-03`, `rls-20260821`, ...) and attribute keys
(`status`, `priority`, `due_date`) are drawn from this vault's actual
frontmatter conventions; substitute your own vault's IDs/keys as needed.

---

## 1. Simple listing use cases (metadata-only, no search index required)

These rely purely on frontmatter/file metadata and work even if
`matlock search index` has never been run (`--search-mode metadata_only`,
which is also the automatic fallback when `--text` is omitted).

### 1.1 "List every document that belongs to project PRJ-26-07."

```bash
matlock --config "$MATLOCK_CFG" documents query --project-id prj-26-07
```

### 1.2 "Show me all Markdown files under the daily notes folder."

```bash
matlock --config "$MATLOCK_CFG" documents query --file-path daily/ --file-ext .md
```

### 1.3 "What documents were modified in the last week of August 2026?"

- [ ] This one did NOT work

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --modified ">=2026-08-25" --modified "<=2026-08-31"
```

### 1.4 "List all documents with status 'Complete' and priority 'high'."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --attributes "status:eq:Complete" --attributes "priority:eq:high"
```

> Note: repeated `--attributes` values AND-compose across different paths
> (each option here targets a different JSON path), so this returns
> documents matching *both* conditions.

### 1.5 "Which projects are part of the 'enhancing' super-project?"

```bash
matlock --config "$MATLOCK_CFG" projects query --super-project-id enhancing
```

### 1.6 "List every file tied to release RLS-20260821."

```bash
matlock --config "$MATLOCK_CFG" documents query --project-id rls-20260821
```

### 1.7 "Give me just the count of documents created in Q1 2026, not the full list."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --created ">=2026-01-01" --created "<2026-04-01" --count-only
```

---

## 2. Exact keyword match use cases (`fts_only`)

Full-text search over indexed chunk content, useful when the user knows a
specific term/phrase and wants literal matches rather than "similar
meaning" results. Requires `matlock search index` to have been run first.

### 2.1 "Find every mention of 'mTLS handshake' in the vault."

```bash
matlock --config "$MATLOCK_CFG" search index          # ensure index is fresh
matlock --config "$MATLOCK_CFG" documents query \
  --text "mTLS handshake" --search-mode fts_only
```

### 2.2 "Which files literally contain 'batch CSV reconciliation'?"

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "batch CSV reconciliation" --search-mode fts_only --granularity chunk
```

Use `--granularity chunk` here to see the exact matched passage, not just
the file it lives in.

### 2.3 "Show me the exact lines around every 'PRJ-26-07' reference, with one chunk of context before/after."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "PRJ-26-07" --search-mode fts_only \
  --granularity chunk --surrounding-chunks 1
```

### 2.4 "Restrict the 'gRPC streams' keyword search to the payment gateway project only."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "gRPC streams" --search-mode fts_only --project-id prj-26-07
```

---

## 3. Conceptual / vector search use cases (`vector_only`)

Semantic similarity search, useful when the user describes an idea in
their own words rather than quoting exact vault terminology.

### 3.1 "Find notes about improving security around service-to-service authentication, even if they don't use the word 'mTLS'."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "improving security for service-to-service authentication" \
  --search-mode vector_only
```

### 3.2 "What documents discuss reducing manual work in month-end financial close, conceptually?"

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "reducing manual effort in month-end financial closing" \
  --search-mode vector_only --min-score 0.4
```

### 3.3 "Find semantically similar maintenance work to 'server inventory cleanup', limited to the mnt-26 projects."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "server inventory cleanup" --search-mode vector_only \
  --project-id mnt-26-03 --project-id mnt-26-04
```

---

## 4. Hybrid search use cases (default mode)

Combines lexical + vector ranking (reciprocal-rank fusion); best default
when the user's phrasing partially matches vault vocabulary but you also
want conceptually related hits ranked in.

### 4.1 "Find anything relevant to 'payment gateway outages', ranked best-first."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "payment gateway outages" --search-mode hybrid
```

(`hybrid` is the default, so `--search-mode hybrid` can be omitted.)

### 4.2 "Search for 'retry queue telemetry' but only in files touched in the last 90 days."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "retry queue telemetry" \
  --modified ">=2026-06-22"
```

### 4.3 "Find the best-matching high-priority documents about 'outbound processor routing'."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "outbound processor routing" \
  --attributes "priority:eq:high" --min-score 0.5
```

---

## 5. Summarization use cases (count-first, then paginate through details)

For "summarize X" style questions, run a cheap `--count-only` call first to
size the result set, then follow up with one or more paginated detail
calls (adjusting `--limit`/`--offset`) to actually retrieve the content to
summarize.

### 5.1 "Summarize everything we've done on PRJ-26-07."

```bash
# Step 1: how much is there?
matlock --config "$MATLOCK_CFG" documents query \
  --project-id prj-26-07 --count-only

# Step 2: pull full file list + metadata (paginate if total_matches > limit)
matlock --config "$MATLOCK_CFG" documents query \
  --project-id prj-26-07 --limit 50 --offset 0

# Step 3: for each file of interest, read full content for summarization
matlock --config "$MATLOCK_CFG" document read projects/prj-26-07/prj-26-07.md
```

### 5.2 "Give me a summary of everything related to mTLS across the whole vault."

```bash
# Step 1: size the result set for a keyword match
matlock --config "$MATLOCK_CFG" documents query \
  --text "mTLS" --search-mode fts_only --count-only

# Step 2: pull matched chunks with surrounding context to summarize from
matlock --config "$MATLOCK_CFG" documents query \
  --text "mTLS" --search-mode fts_only \
  --granularity chunk --surrounding-chunks 1 --limit 25 --offset 0

# Step 3 (if total_matches > limit): repeat with incremented --offset
matlock --config "$MATLOCK_CFG" documents query \
  --text "mTLS" --search-mode fts_only \
  --granularity chunk --surrounding-chunks 1 --limit 25 --offset 25
```

### 5.3 "Summarize the current status of all high-priority projects."

```bash
# Step 1: how many high-priority projects are there?
matlock --config "$MATLOCK_CFG" projects query --priority high --count-only

# Step 2: list them with status detail
matlock --config "$MATLOCK_CFG" projects query --priority high --limit 20

# Step 3: read each project's home file for narrative status/detail
matlock --config "$MATLOCK_CFG" document read projects/prj-26-07/prj-26-07.md
```

### 5.4 "What happened across all releases in Q3 2026? Give me a summary."

```bash
# Step 1: size the set of release-tagged documents touched in Q3
matlock --config "$MATLOCK_CFG" documents query \
  --project-id rls-20260821 --modified ">=2026-07-01" --modified "<2026-10-01" \
  --count-only

# Step 2: list matching documents
matlock --config "$MATLOCK_CFG" documents query \
  --project-id rls-20260821 --modified ">=2026-07-01" --modified "<2026-10-01" \
  --limit 50

# Step 3: conceptually pull in related discussion even without exact wording
matlock --config "$MATLOCK_CFG" documents query \
  --text "release rollout summary and outcomes" --search-mode hybrid \
  --project-id rls-20260821
```

### 5.5 "Is there a lot of content on server maintenance, or just a little?"

```bash
# Quick scale check before deciding whether to summarize or just answer directly
matlock --config "$MATLOCK_CFG" documents query \
  --text "server maintenance" --search-mode vector_only --count-only
```

If `total_matches` is small (e.g. under ~5), skip straight to listing
full results instead of paginating:

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "server maintenance" --search-mode vector_only --limit 5
```

---

## 6. Combined metadata + keyword/vector use cases

### 6.1 "Find mentions of 'reconciliation' only in completed, high-priority projects."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "reconciliation" --search-mode fts_only \
  --attributes "status:eq:Complete" --attributes "priority:eq:high"
```

### 6.2 "Search for content about 'inventory service' but only within .md files directly under projects/."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "inventory service" --search-mode hybrid \
  --file-path projects/ --file-ext .md
```

### 6.3 "Find task-related notes about 'estimate overrun' created after May 2026, across the enhancing super-project."

```bash
matlock --config "$MATLOCK_CFG" documents query \
  --text "estimate overrun" --search-mode hybrid \
  --super-project-id enhancing --created ">=2026-05-01"
```

---

## 7. Task-oriented use cases (complementary `tasks query` commands)

Not document search, but frequently paired with the summarization flow
above when the user's question is really about outstanding work.

### 7.1 "What tasks are still open for PRJ-26-07?"

```bash
matlock --config "$MATLOCK_CFG" tasks query --project-id prj-26-07 --unchecked
```

### 7.2 "Summarize completed work this quarter — how many tasks, then list them."

```bash
matlock --config "$MATLOCK_CFG" tasks query \
  --checked --act-comp-date ">=2026-07-01" --act-comp-date "<2026-10-01" --count-only

matlock --config "$MATLOCK_CFG" tasks query \
  --checked --act-comp-date ">=2026-07-01" --act-comp-date "<2026-10-01" --limit 50
```

---

## Search mode quick reference

| User intent | `--search-mode` | Requires `search index`? |
|:------------|:-----------------|:-------------------------|
| Browse/filter by metadata only (no free text) | `metadata_only` (automatic when `--text` omitted) | No |
| "Find the exact phrase/term ..." | `fts_only` | Yes |
| "Find things conceptually similar to ..." | `vector_only` | Yes |
| "Find the best matches for ..." (default, blends both) | `hybrid` | Yes |

## Pagination pattern for summaries

1. Run the same filter/query with `--count-only` to get `total_matches`.
2. If `total_matches` is small, fetch it all in one call with a generous `--limit`.
3. If large, loop `--limit N --offset 0, N, 2N, ...` until `returned_matches` totals `total_matches`.
4. For content-heavy summaries, add `--granularity chunk --surrounding-chunks 1-3` to pull enough surrounding text without reading whole files; fall back to `matlock document read <path>` for files needing full context.
