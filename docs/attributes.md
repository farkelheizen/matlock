# Content attributes

The index can associate JSON data with indexed document bodies and chunks.
It does not scan for secrets or other private information. A separate local
process can inspect the database and record its own findings; operators are
responsible for installing, scheduling, securing, and monitoring that process.
The database, its WAL, source paths, metadata, attributes, and search output
may all contain private information.

## Identity

Ingestion decodes source bytes as UTF-8 with an optional leading BOM and
removes YAML frontmatter as described in [Python internals](python-internals.md).
`source_documents.body` is the resulting text passed to chunking, including
its remaining line endings. `body_sha256` hashes that text encoded as UTF-8.
`chunks.content_sha256` similarly hashes the entire stored chunk, including
heading breadcrumbs, before any response truncation. These hashes are
lowercase SHA-256 hex strings. The existing `source_documents.sha256` still
hashes the original bytes and controls incremental ingestion.

`content_attributes` holds one JSON object per `(domain, sha256)`, where
`domain` is `document` or `chunk`. Identical text within a domain shares the
same object, even across files. The domains do not share attributes with each
other. Missing rows mean no attributes have been recorded; `{}` means a row
exists but has no keys. A present key with JSON `null` is different from a
missing key. The intended timestamp convention is UTC RFC 3339 text, updated
by the writer whenever it changes the object.

Attributes remain if the last matching document or chunk disappears. A
result written after an edit remains attached to the old digest; it cannot
describe the new body or chunk. Operators may delete unreferenced attributes
in a write transaction after coordinating with their external writers:

```sql
BEGIN IMMEDIATE;
DELETE FROM content_attributes
WHERE (domain = 'document' AND NOT EXISTS (
    SELECT 1 FROM source_documents d WHERE d.body_sha256 = content_attributes.sha256
)) OR (domain = 'chunk' AND NOT EXISTS (
    SELECT 1 FROM chunks c WHERE c.content_sha256 = content_attributes.sha256
));
COMMIT;
```

Cleanup removes reusable findings, and a writer still processing old text may
recreate them. To avoid that, stop writers before cleanup. Neither content
hash covers document metadata, frontmatter, or paths. A frontmatter-only edit
does not invalidate body findings, and a content-only review is not evidence
that these other fields are safe.

## Direct SQLite workflow

An external writer must use a version-2 database and a read-write SQLite
connection with a bounded busy timeout. Never modify ingestion's other tables
or virtual indexes. For example, identify current bodies not yet assigned the
top-level key `review`:

```sql
SELECT DISTINCT d.body_sha256, d.body
FROM source_documents AS d
LEFT JOIN content_attributes AS a
    ON a.domain = 'document' AND a.sha256 = d.body_sha256
WHERE json_type(a.attributes_json, '$.review') IS NULL;
```

For chunks, use `chunks.content_sha256`, `chunks.content`, and domain `chunk`
instead. Use the exact returned text and digest as a pair; do not identify
work by path or chunk ID alone. A writer can put its version, status and
results in its own JSON value to decide when reevaluation is needed.

To update one key without losing another writer's keys, bind `:domain`,
`:sha256`, `:json_path`, and `:json_value` to a domain, the evaluated hash,
a top-level JSON path such as `$.review`, and a JSON-encoded value:

```sql
BEGIN IMMEDIATE;
INSERT INTO content_attributes(domain, sha256, attributes_json, updated_at)
VALUES (:domain, :sha256, json_set('{}', :json_path, json(:json_value)),
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
ON CONFLICT(domain, sha256) DO UPDATE SET
    attributes_json = json_set(content_attributes.attributes_json,
                               :json_path, json(:json_value)),
    updated_at = excluded.updated_at;
COMMIT;
```

Each process must reserve a distinct top-level key, validate that key as an
ASCII identifier (`[A-Za-z_][A-Za-z0-9_]*`) before constructing its bound
JSON path, and pass valid JSON as `:json_value`. Nested values may be any
valid JSON. Bind parameters rather than concatenating values into SQL.
`BEGIN IMMEDIATE` serializes writers; on timeout, invalid data, or any other
error roll back, report the failure, and retry according to the process's
own policy. A successful upsert changes the timestamp even when replacing a
value with an identical one. The database enforces domain, hash shape, and
object validity, but external writers must supply a correct digest and UTC
timestamp. Do not copy a finding to a different digest or mark missing work
as complete. MCP readers use read-only connections and see committed changes
on their next request.

## Migration

Back up the database, including a consistent WAL snapshot, before upgrading.
The ingestion process migrates schema 1 to 2 on opening the index. The
migration reads each source from the configured vault and verifies its
original byte hash before recovering the body. If a file is missing, changed,
unreadable, or invalid, migration rolls back and reports the affected source;
restore the exact indexed version and retry. Existing chunk, full-text, and
vector rows are not rebuilt. Migrated entries have no attribute records.
Upgrade ingestion and MCP readers together: schema-2 readers reject schema 1,
and older readers and writers do not support schema 2. Restore the backup
with matching old software if rollback to the old version is needed.

## The `secret_scan` key

`matlock2-scanner` records its findings under the top-level key `secret_scan`
(configurable). It only inserts rows for content that has none, so a row from
another writer prevents scanning. See the [secret scanner guide](secret-scanner.md)
for the stored fields, the one-scan-per-hash rule, and size limits.
