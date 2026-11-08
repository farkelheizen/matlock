# Copilot instructions

- Consult `docs_local/` for requirements and project guidance that must remain local and must never be added to source control.
- Treat those local requirements as inputs for implementation, not as content to copy into tracked source files, tests, comments, documentation, commit messages, or other source-controlled artifacts.
- Keep `docs_local/` ignored. Do not remove its ignore rule, replace the symlink with a tracked copy, or commit files from its target.
- If a task appears to require publishing local-only requirement text, ask for clarification before doing so.
- Update `docs/CHANGELOG.md` under Unreleased when making notable project changes, using the existing changelog format.
- During autonomous work, commit frequently at coherent, verified milestones. Keep commits focused and do not include unrelated or local-only changes.
