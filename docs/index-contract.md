# SQLite index contract

The `matlock2_index` package defines the SQLite boundary shared by ingestion,
the secret scanner, and MCP search:

- `SCHEMA_VERSION` identifies the schema that consumers can safely use.
- `connect_index` provides shared busy-timeout and read-only connection setup.
- `validate_index_schema` checks required tables, columns, and schema version.
- Table-name constants identify the records and indexes in that contract.

The package is intentionally small. It does not own application configuration,
ingestion behavior, search policy, or scanner behavior, and it does not expose
a general-purpose ORM or repository abstraction.

## Ownership

`matlock2_ingest` is the schema writer: it creates tables, upgrades prior
versions, and atomically updates documents, chunks, FTS rows, and vectors.
`matlock2_scanner` reads indexed content and writes only scanner annotations
to `content_attributes`. `matlock2_mcp` uses the shared read-only connection
and validation helpers; it does not migrate or modify the index.

Consumers must validate the schema before using it. Schema version 2 includes
content-addressed document and chunk bodies and the `content_attributes` table.
Version 1 databases are upgraded by ingestion; scanner and MCP consumers fail
with an actionable incompatibility error rather than attempting a migration.

## Changing the contract

When changing a shared table or column:

1. Update `matlock2_index`'s version and required schema in the same change as
   the ingestion migration.
2. Keep the migration transactional and make it safe for existing supported
   databases.
3. Verify ingestion, scanner, and MCP behavior against both the migrated
   database and the current schema.
4. Update [the schema reference](database.md) and this ownership description.

The `matlock2_index` package is a shared internal boundary in the current
distribution, not a separately versioned or independently released library.
