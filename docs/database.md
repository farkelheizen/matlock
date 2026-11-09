# SQLite database structure

This describes the current schema version 2, not a guaranteed stable public
API. The ingestion engine owns writes; external consumers should open the
existing database read-only. The database contains the indexed note text and
metadata, so protect it and its WAL files as private data.

## Relationships

```text
source_documents.path  1 ---- * chunks.source_path
                                   |
                                   +-- chunks.id = chunks_fts.rowid
                                   +-- chunks.id = vec_chunks.rowid
```

`chunks.source_path` has a foreign key to `source_documents.path` with cascade
deletion. The FTS and vector associations are maintained by ingestion code,
not foreign keys or triggers: directly editing or deleting rows can leave
indexes inconsistent. File replacement removes and recreates its chunk/index
rows in a single transaction.

| Table | Columns and purpose |
| --- | --- |
| `database_metadata` | `key` (primary key), `value`. Holds `schema_version` and `vector_dimensions` as text. |
| `source_documents` | `path` (vault-relative primary key), `sha256` (raw file bytes), `body`, `body_sha256`, `processing_fingerprint`, `metadata_json` (valid JSON text), `embedding_model`, `embedding_version`, `ingested_at` (UTC ISO timestamp). |
| `chunks` | `id` (integer primary key), `chunk_id` (unique public identifier), `source_path`, `ordinal`, `content`, `content_sha256`. `(source_path, ordinal)` is unique. |
| `content_attributes` | `domain` (`document` or `chunk`), `sha256`, JSON object in `attributes_json`, and `updated_at`. The domain and hash form the primary key. |
| `chunks_fts` | FTS5 virtual table with unindexed `chunk_id` and indexed `content`; rowid matches `chunks.id`. |
| `vec_chunks` | `sqlite-vec` `vec0` virtual table with a fixed-dimension float embedding; rowid matches `chunks.id`. |
| `ingestion_errors` | `id`, `source_path`, `source_hash`, `stage`, `error_details`, `attempt_count`, `first_seen_at`, `last_attempt_at`, `status`; the `(source_path, source_hash, stage)` tuple is unique. Status is `unresolved`, `resolved`, or `superseded`. |

`chunks_source_path_idx` indexes chunk ownership and
`ingestion_errors_status_idx` indexes error status. SQLite's virtual tables
may also create internal shadow tables; these are implementation details.

Metadata is stored once per document; join through `chunks.source_path` to
include it with chunk results. A chunk ID derives from its path and ordinal
and is reproducible for the same path/order, but content edits can change
what a particular ordinal represents. Numeric `chunks.id` values are
recreated on re-ingestion and must not be used as stable external IDs.

The body and chunk digests hash UTF-8 encoded indexed text. The body excludes
frontmatter, while the raw-file digest still controls ingestion. Attributes
remain after content is removed. Schema 1 databases are upgraded by ingestion
in a transaction; each source must still exist and match its indexed raw-file
hash. Back up the database first and restore the indexed source versions if
the upgrade reports an error. Older software cannot read the upgraded schema.

The database records a model name and package version per source. A scan that
changes model settings can leave a mixture of successfully reindexed and
older documents if individual files fail. Do not compare vectors from
incompatible embedding spaces. A changed vector dimension requires a new or
migrated database. The stored version is a FastEmbed/tokenizers package
version string, not a cryptographically pinned model revision.

SQLite uses WAL and a configured busy timeout for ingestion connections.
FTS scores from `bm25` are ordered ascending (lower is better). Vector
distances are distances, not normalized relevance scores; cosine distance is
smaller for more similar vectors.

## Query it

Follow [the example walkthrough](example.md), then run the
[metadata, FTS, and vector examples](queries.md). Loading `sqlite-vec` is
required on each connection that uses its vector functions.
