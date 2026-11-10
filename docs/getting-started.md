# Getting started

## Install

Open a terminal in the Git repository root, the directory containing
`pyproject.toml`. On macOS or Linux:

```sh
cd /path/to/matlock2
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

If you already created the repository's `.venv`, activate it instead of
creating it again. The local `docs_local` link is a separate documentation
directory, not the repository root.

Optional extras are installed the same way, for example
`python -m pip install -e ".[mcp]"` for the [MCP search tools](mcp.md),
`".[scanner]"` for the heuristic [secret scanner](secret-scanner.md), and
`".[scanner-llm]"` to add its local LLM layer.

Python 3.11 or later is required. The first run that initializes the configured
embedding model may need network access to download model files. Embedding
inference runs locally. SQLite must support loading the required `sqlite-vec`
extension.

## Configure and run

From the repository root:

```sh
cp matlock2.toml.example matlock2.toml
```

Edit `matlock2.toml` to set `vault_path` to an existing directory containing
Markdown and `database_path` to the desired SQLite file. The root
`matlock2.toml` and `*.sqlite3` database files are ignored by Git; do not put
secrets in tracked examples.

```sh
matlock2 --config matlock2.toml check-config
matlock2 --config matlock2.toml scan
matlock2 --config matlock2.toml watch
```

Run `scan` for one pass, or `watch` to keep the process running. Stop the
watcher with Ctrl-C. A watcher starts with a scan and periodically repeats it.
See [configuration](configuration.md) and [operations](operations.md) for
options and behavior.

To try this without pointing at your own notes, follow the
[fictional second-brain example](example.md).

To scan indexed content for secrets, see the [secret scanner](secret-scanner.md).
