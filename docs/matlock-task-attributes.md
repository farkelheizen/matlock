# Writing Task Attributes in Markdown

This doc is for **vault authors** — it describes the syntax you can use inside a
task line in your Markdown notes to attach structured metadata (due dates,
priority, time estimates, etc.) that Matlock parses into the `task` table.

For how attributes are *defined and configured* (adding new attribute types,
changing aliases), see [`docs/matlock-configuration.md`](matlock-configuration.md#task-attribute-definition).

---

## Two Ways to Add an Attribute

Every attribute can be written one of two ways inside a task's text:

1. **Alias shorthand** — a short symbol (usually an emoji) immediately
   followed by the value, e.g. `📅 2026-06-01`.
2. **Curly-brace form** — `{ attribute_name: value }`, always available even
   if no alias is configured, e.g. `{ due_date: 2026-06-01 }`.

Both forms are stripped from `task_text` after parsing and stored separately
in the task's `attributes`.

```markdown
- [ ] Ship the release notes 📅 2026-06-01 ⏫
- [ ] Ship the release notes { due_date: 2026-06-01 } { priority: high }
```

Both lines above produce the same parsed result:

- `task_text`: `Ship the release notes`
- `attributes`: `{"due_date": "2026-06-01", "priority": "high"}`

---

## Default Attributes

These attributes ship in `config/default_config.json`. Your vault's
`config.yaml` may define additional or different ones under `task_attributes`
(see [`matlock-configuration.md`](matlock-configuration.md)).

| Attribute | Type | Alias | Example (alias) | Example (curly-brace) |
|:----------|:-----|:------|:-----------------|:------------------------|
| `due_date` | `date` | 📅 | `📅 2026-06-01` | `{ due_date: 2026-06-01 }` |
| `complete_date` | `date` | ✅ | `✅ 2026-05-28` | `{ complete_date: 2026-05-28 }` |
| `priority` | `domain` (`low`, `medium`, `high`) | 🔽 / 🔼 / ⏫ | `⏫` | `{ priority: high }` |
| `estimate` | `time` | *(none by default)* | — | `{ estimate: 2h30m }` |
| `actual` | `time` | *(none by default)* | — | `{ actual: 45m }` |

> Some example vault configs (see `docs/matlock-configuration.md`) also define
> `created_date` (➕) and `est_comp_date` (🏁) — check your own `config.yaml`
> for the exact set active in your vault.

---

## Attribute Types & Value Syntax

### `date`

Must be `YYYY-MM-DD`. Anything else is rejected (the attribute is dropped and
a parse error is recorded; the task itself is still saved).

```markdown
- [ ] Renew passport 📅 2026-09-30
- [ ] Renew passport { due_date: 2026-09-30 }
```

Invalid:

```markdown
- [ ] Renew passport 📅 09/30/2026   <!-- wrong format, attribute dropped -->
```

### `time`

A human-readable duration string, parsed with `pytimeparse` and stored as an
integer number of **seconds**. Supports combined units like `1h30m`.

```markdown
- [ ] Refactor parser module { estimate: 2h }
- [ ] Refactor parser module { estimate: 1h30m }
- [ ] Refactor parser module { actual: 45m }
```

`{ estimate: 2h }` is stored as `7200`.

### `domain`

A closed set of allowed values, optionally each with its own literal alias
(no value needed after the alias — the alias *is* the value).

```markdown
- [ ] Fix login bug ⏫                     <!-- alias for priority: high -->
- [ ] Fix login bug { priority: high }     <!-- explicit form -->
```

Using a value outside the configured set (e.g. `{ priority: urgent }` when
only `low`/`medium`/`high` are defined) drops the attribute and records an
error.

### Plain string (no configured type)

Any `{ key: value }` pair whose `key` isn't declared in `task_attributes` is
still captured as a raw string under that key — useful for ad-hoc metadata.

```markdown
- [ ] Review PR { reviewer: alice }
```

---

## Combining Multiple Attributes

Attributes can be freely mixed and ordered on the same line, in either form:

```markdown
- [ ] Prepare Q3 board deck 📅 2026-09-15 ⏫ { estimate: 3h }
```

Parses to:

- `task_text`: `Prepare Q3 board deck`
- `attributes`: `{"due_date": "2026-09-15", "priority": "high", "estimate": 10800}`

---

## Rules & Gotchas

- **One value per attribute per task.** If the same attribute appears twice
  (alias or curly-brace, in any combination), the second occurrence is
  ignored and a `Duplicate attribute ... encountered` error is recorded.
- **Alias value boundary.** For alias-based attributes, the value text runs
  from right after the alias up to the next alias or the next `{` on the
  line — keep other attributes after it, not interleaved mid-value.
- **Errors don't block the task.** Invalid or duplicate attributes are
  dropped and logged in `errors`; the task is still saved with its remaining
  valid attributes.
- **Attributes are stripped from the visible task text** shown in reports and
  search — write them anywhere convenient in the line.
