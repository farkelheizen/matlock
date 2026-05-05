# Security and Privacy Notes

Matlock is designed to be public code that operates on private Markdown vault data.

## Recommended Separation

- Keep this repository for code only.
- Keep your actual vault, `config.yaml`, SQLite database, logs, and generated `_Matlock/` output outside the public repository or in a separate private repository.
- Do not commit notes, generated reports, or backups produced from personal or company data.

## Local Artifacts

The repository ignores common local artifacts used during real deployments, including:

- `config.yaml`
- `config.yaml.*.bak`
- `*.db`, `*.sqlite`, `*.sqlite3`
- `*.log`
- `_Matlock/`

Review `git status` before every push anyway. Ignore rules reduce mistakes; they do not replace review.

## Company and Sensitive Data

If you use Matlock with employer data, confirm your company's policies for:

- open-source tool usage
- personal productivity systems handling company information
- source code and IP ownership
- storage or synchronization of company notes on personal devices

Matlock can process note content and generate derived reports, so the safest default is to treat vault data and generated output as sensitive.

## Reporting

If you discover a security issue in the code, avoid publishing sensitive details in a public issue until the impact is understood and a fix is available.
