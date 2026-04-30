# Copilot Docs Reference Map (matlock)
> Docs baseline: 0.2.x

Use this file as the fast lookup index before implementation.

## Quick Routing Rules

1. **Need architecture context?**
   - Open: `docs/matlock-high-level-design.md`
2. **Need pipeline stage details (sync, parse, map-projects, rollup, report, server)?**
   - Open: `docs/matlock-pipeline-specification.md`
3. **Need data model / SQLite schema?**
   - Open: `docs/matlock-data-model.md`
4. **Need config.yaml schema (paths, task attributes, projects)?**
   - Open: `docs/matlock-configuration.md`
5. **Need report template structure or dashboard fields?**
   - Open: `docs/matlock-generated-reports.md`
6. **Need CLI command reference (flags, entrypoint)?**
   - Open: `docs/matlock-cli.md`
7. **Need `scan-projects` command details (scanning logic, output modes, YAML schema)?**
   - Open: `docs/matlock-scan-projects.md`
8. **Need the implementation roadmap or a phase plan?**
   - Open: `docs/roadmap/index.md`, then the relevant phase plan doc.

---

## Topic → Primary Doc → Supporting Docs

| Topic | Primary Doc | Supporting Docs |
|---|---|---|
| Architecture overview, 5-stage pipeline, execution modes, directory guardrail, discovery commands | `docs/matlock-high-level-design.md` | `docs/matlock-pipeline-specification.md` |
| Pipeline stage logic: sync, parse, map-projects, rollup, report, server | `docs/matlock-pipeline-specification.md` | `docs/matlock-data-model.md`, `docs/matlock-high-level-design.md` |
| SQLite schema, in-memory models, table columns, PRAGMA config | `docs/matlock-data-model.md` | `docs/matlock-pipeline-specification.md` |
| config.yaml schema, task attributes, project definitions, path resolution, logging | `docs/matlock-configuration.md` | `docs/matlock-data-model.md` |
| Jinja2 report templates, dashboard types, template variables, heatmap logic | `docs/matlock-generated-reports.md` | `docs/matlock-high-level-design.md` |
| CLI commands, flags, `matlock server`, entrypoint registration | `docs/matlock-cli.md` | `docs/matlock-pipeline-specification.md` |
| `scan-projects`: scanning logic, ScannedFile, ProjectCandidate, output modes, YAML schema | `docs/matlock-scan-projects.md` | `docs/matlock-cli.md`, `docs/matlock-configuration.md` |
| Implementation phases, roadmap overview | `docs/roadmap/index.md` | Phase plan docs in `docs/roadmap/` |

---

## Keyword Index (use when searching)

- **scan-projects, ScannedFile, ProjectCandidate, --print-yaml, --diff, --merge, scan_vault, merge_into_config** → `matlock-scan-projects.md`, `matlock-cli.md`
- **sync, needs_parsing, SHA-256, file watcher, deleted flag** → `matlock-pipeline-specification.md`, `matlock-data-model.md`
- **parse, extract_tasks_from_markdown, ParsedMarkdownFile, ParsedMarkdownTask** → `matlock-pipeline-specification.md`, `matlock-data-model.md`
- **map-projects, file_project, project, super_project, resources, DIRECTORY, FILE** → `matlock-pipeline-specification.md`, `matlock-data-model.md`, `matlock-configuration.md`
- **rollup, daily_metric, streak, heatmap, nightly** → `matlock-pipeline-specification.md`, `matlock-data-model.md`
- **report, Jinja2, _Matlock/, dashboard, is_generated** → `matlock-generated-reports.md`, `matlock-pipeline-specification.md`
- **server, watchdog, debouncer, scheduler, daemon, midnight** → `matlock-cli.md`, `matlock-pipeline-specification.md`
- **config.yaml, base_directory, output_directory, db_path, debounce_seconds** → `matlock-configuration.md`
- **log_path, log_max_bytes, log_backup_count, logging, rotating log** → `matlock-configuration.md`, `matlock-high-level-design.md`
- **task_attributes, due_date, priority, alias, domain, date, time** → `matlock-configuration.md`, `matlock-data-model.md`
- **task_id, twin_index, overflow, headers, parent_task_id** → `matlock-data-model.md`
- **SQLite, WAL, PRAGMA, foreign_keys, busy_timeout** → `matlock-data-model.md`
- **run-all, full pipeline, skip-rollup, force-sync** → `matlock-cli.md`, `matlock-high-level-design.md`
- **roadmap, phase plan, implementation steps** → `docs/roadmap/index.md`

---

## Copilot Usage Protocol

When implementing or fixing code:

1. Open **one primary doc** from the Topic table above.
2. Open **1–2 supporting docs** only if needed.
3. Extract explicit constraints before coding (table columns, flag names, template variable names, validation rules).
4. If docs conflict, prefer the more specific spec:
   - Stage behaviour → `matlock-pipeline-specification.md`
   - Schema / models → `matlock-data-model.md`
   - Config fields → `matlock-configuration.md`
5. Record which docs were used in the phase plan's Step Notes Log when a decision is non-obvious.
