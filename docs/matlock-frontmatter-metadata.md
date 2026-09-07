# Writing Frontmatter Metadata in Markdown

This doc is for **vault authors** — it describes the YAML frontmatter keys
Matlock recognizes by default on project/definition pages, used by
`matlock scan-projects` to reverse-engineer your `projects` /
`super_projects` config, and by search chunking's `fm.*` template namespace.

For the task-line syntax (due dates, priority, estimates on individual
`- [ ]` tasks), see [`docs/matlock-task-attributes.md`](matlock-task-attributes.md).
For how discovered projects become `config.yaml` entries, see
[`docs/matlock-scan-projects.md`](matlock-scan-projects.md).

---

## How Frontmatter Is Read

- Frontmatter keys are matched **case-insensitively** (`Priority`, `PRIORITY`,
  and `priority` are equivalent). The first occurrence of a lower-cased key
  wins, except `projects`/`Projects`, whose values are **combined**.
- Files with no frontmatter block (or an empty one) are skipped entirely by
  the scanner.
- Bare `YYYY-MM-DD` YAML values are parsed as dates automatically by PyYAML;
  Matlock coerces these transparently to ISO 8601 strings, so you can quote
  them or leave them bare.

```markdown
---
title: Taxes 2026
priority: high
status: in progress
start_date: 2026-01-01
due_date: 2026-04-30
---
```

---

## Recognized Keys

| Key | Type | Purpose | Notes |
|:----|:-----|:--------|:------|
| `tag` / `tags` | string / list | Marks a page as a project | Matches if `tag: project` or `tags` list contains `project` (case-insensitive) |
| `title` | string | Project display title | Used as-is |
| `super_project_id` | string | Links a project to a super-project | See `parent` fallback below |
| `parent` | string (wikilink) | Fallback for `super_project_id` | Only used when `super_project_id` is absent; format `[[Name, Super-Project]]` (optional `\|alias`) |
| `priority` | string | Project priority | One of `low`, `medium`, `high` (case-insensitive) → normalised to `Low`/`Medium`/`High`. Other values produce a scan warning. |
| `status` | string | Project status | One of `planned`, `in progress`, `on hold`, `complete`, `cancelled` (case-insensitive) → normalised to title case. Other values produce a scan warning. |
| `started` / `start_date` / `start date` | string or date | Project start date | First match wins, in that order; falls back to the file's creation date if none are present |
| `due_date` / `due date` / `est_comp` / `est comp` | string or date | Project due date | First match wins, in that order |
| `resources` / `related` | list | Files/directories attributed to this project | Each entry is a path string, or `{path: ..., type: FILE\|DIRECTORY}`; resolved against the vault and deduplicated by path |
| `projects` / `Projects` | list | Attributes *this* page as a resource to other projects | Values from both key casings are combined; the page becomes a `FILE` resource for every listed project ID |

---

## Field Details & Examples

### `tag` / `tags` — marking a project page

```yaml
---
tag: project
---
```

or

```yaml
---
tags:
  - project
  - work
---
```

### `title`

```yaml
---
title: Backend API Overhaul
---
```

### `super_project_id` (and `parent` fallback)

Explicit form:

```yaml
---
super_project_id: Work
---
```

Fallback via wikilink `parent` (only consulted when `super_project_id` is
absent and the page is already a recognized project page):

```yaml
---
parent: "[[Work, Super-Project]]"
---
```

### `priority`

```yaml
---
priority: high      # → High
---
```

Invalid values (e.g. `priority: urgent`) are ignored and recorded as a scan
warning.

### `status`

```yaml
---
status: in progress  # → In Progress
---
```

Valid values: `Planned`, `In Progress`, `On Hold`, `Complete`, `Cancelled`
(matched case-insensitively).

### `started` / `start_date` / `start date`

Any of these three key spellings is accepted; the first one present wins.

```yaml
---
start_date: 2026-01-01
---
```

If none are present, Matlock falls back to the file's creation timestamp.

### `due_date` / `due date` / `est_comp` / `est comp`

Any of these four key spellings is accepted; the first one present wins.

```yaml
---
due_date: 2026-04-30
---
```

### `resources` / `related`

Plain path strings:

```yaml
---
resources:
  - Tech/Backend
  - Projects/Backend_Notes.md
---
```

Explicit type (overrides auto-detection; must match the actual filesystem
type or a warning is recorded):

```yaml
---
related:
  - path: Tech/Backend
    type: DIRECTORY
  - path: Projects/Backend_Notes.md
    type: FILE
---
```

Paths that don't resolve to an existing file or directory are dropped with a
warning.

### `projects` — attributing a non-project page to other projects

Use this on a page that *isn't* itself a project's home page, to attach it as
a resource to one or more existing projects:

```yaml
---
projects:
  - Backend API
  - Website Overhaul
---
```

Both `projects` and `Projects` casing keys are read and their values merged.

---

## Notes on Directory & Filename Conventions

These are not frontmatter keys, but combine with the keys above to determine
`project_id`:

| Convention | Rule |
|:-----------|:-----|
| Directory pattern | `Projects/<Name>/<Name>.md` (or `projects/`) → `project_id = <Name>` |
| Named-file pattern | Filename ends with `, Project.md` or `, Master Project.md` → `project_id` = text before the suffix |
| Tag-based | `tag`/`tags` contains `project` with no directory/filename match → `project_id` = file stem |

See [`docs/matlock-scan-projects.md`](matlock-scan-projects.md) for full
precedence rules and the resulting `config.yaml` shape.

---

## Search Chunking Template Access

If `search.chunking.inject_frontmatter` is enabled, any frontmatter field on
the file being chunked is available in report/search templates via the
`fm.*` namespace (e.g. `{fm.status}`), independent of whether the key is one
of the project-scan keys above. See
[`docs/matlock-configuration.md`](matlock-configuration.md#search-chunking)
for the template syntax.
