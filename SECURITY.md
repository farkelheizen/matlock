# Security and Privacy Notes

Matlock is designed to be public code that operates on private Markdown vault data.

Matlock processes note content, task metadata, project mappings, search indexes, logs, and generated reports. Treat all vault data and derived artifacts as sensitive unless you have explicitly reviewed their contents and access controls.

## Recommended Separation

- Keep this repository for code only.
- Keep your actual vault, `config.yaml`, SQLite database, logs, and generated `_Matlock/` output outside the public repository or in a separate private repository.
- Do not commit notes, generated reports, or backups produced from personal or company data.
- Keep the Matlock SQLite database, search index, redaction cache, logs, and generated `_Matlock/` reports outside the public repository.
- Restrict access to the vault and its Matlock configuration. The configuration can contain absolute paths and deployment-specific settings even when it does not contain credentials.

## Local Artifacts

The repository ignores common local artifacts used during real deployments, including:

- `config.yaml`
- `config.yaml.*.bak`
- `*.db`, `*.sqlite`, `*.sqlite3`
- `*.log`
- `_Matlock/`

Review `git status` before every push anyway. Ignore rules reduce mistakes; they do not replace review.

## Secret Detection and Redaction

- The parse stage records document-level secret-detection state for tracked files.
- `matlock secrets backfill` can resolve unknown states or retry scanner errors without re-parsing tasks.
- `matlock document read` and search query flows use redacted content when a document is unsafe. A redaction cache is still sensitive because it is derived from private source material.
- Secret detection is a safety layer, not a guarantee that every secret will be identified. Review configuration, scanner behavior, logs, and generated reports before sharing them.

Do not use raw file reads, ad hoc database queries, copied search-index files, or debug output to bypass the normal redaction paths when working with sensitive vault data.

## Search and Generated Output

- Search indexing stores derived text, metadata, full-text search data, and optional embeddings in SQLite. Protect the database with the same care as the source vault.
- Generated Markdown reports may reproduce task text, project names, dates, and aggregate activity. Review `_Matlock/` output before publishing or synchronizing it.
- Logs can contain file paths, warning text, and operational details. Keep configured log paths private and rotate or delete them according to your organization’s retention requirements.

## Company and Sensitive Data

If you use Matlock with employer data, confirm your company's policies for:

- open-source tool usage
- personal productivity systems handling company information
- source code and IP ownership
- storage or synchronization of company notes on personal devices

Matlock can process note content and generate derived reports, so the safest default is to treat vault data and generated output as sensitive.

## Reporting

If you discover a security issue in the code, avoid publishing sensitive details in a public issue until the impact is understood and a fix is available.
