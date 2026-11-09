# Querying the example database

First install the project and run the [example scan](example.md). From the
repository root:

```sh
python examples/second-brain/query.py
```

The script opens the generated database read-only and demonstrates all three
query types. It uses bound SQL parameters and prints source paths plus data.
Use `--database /path/to/index.sqlite3` to select another database, but do not
run the fixed demonstration query on sensitive data unless appropriate.

## Metadata

The first query uses
`json_extract(metadata_json, '$.project_id') = ?` with `atlas`. It returns
the decision and overview documents, but not the retrospective, whose
frontmatter explicitly supplies a different ID. Metadata is document-level;
join `source_documents.path = chunks.source_path` to return matching chunks.

## Full-text

The second query uses `chunks_fts MATCH ?` with `migration` and joins its
rowid to `chunks.id`. It returns the matching chunk from the decision note.
`bm25(chunks_fts)` ranks lower values first. FTS5 has its own query syntax;
binding a value prevents SQL injection but does not turn an FTS expression
into literal text or make malformed FTS syntax valid.

## Vector

The third query loads `sqlite-vec` into the connection, embeds a short query
using the indexed model, and orders chunk rows by
`vec_distance_cosine(v.embedding, ?)`. It binds a serialized float32 vector
and returns the five closest rows; smaller distance means closer. This
example scans and ranks all stored vectors and is intended for a small
database, not as a scalable nearest-neighbor implementation.

The natural-language query is embedded using the same model and FastEmbed
method as ingestion. The script refuses a mixed-model or package-version
database instead of silently comparing incompatible vectors. Model files may
need to be downloaded on first use; the example config specifies the default
512-token input limit. It does not prove that the underlying model revision
matches because the current database stores package versions, not a pinned
model artifact. See [database structure](database.md) for those limitations.

These direct SQL examples are exploratory, not MCP tools or a stable search
API.

## Secret scanner findings

Queries for `secret_scan` results are in the
[secret scanner guide](secret-scanner.md#reading-findings) and the
[example](secret-scanner-example.md).
