# Matlock2

Matlock2 is a Python command-line application for indexing Markdown files in
SQLite.

## Setup

Python 3.11 or newer is required. Install the project and its dependencies:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

The configured embedding model is downloaded on first use.

## Configure

Copy `matlock2.toml.example` to `matlock2.toml` and edit the paths and settings
for your environment. Hook callables are listed under `[hooks]` using
`module:function` names.

## Run

```sh
matlock2 --config matlock2.toml check-config
matlock2 --config matlock2.toml scan
matlock2 --config matlock2.toml watch
```

`scan` performs one pass. `watch` runs continuously until interrupted.

## Tests

Run the self-contained suite with:

```sh
python -m pip install -e '.[dev]'
pytest
```

Tests use temporary databases and synthetic Markdown files. Set
`MATLOCK2_TEST_VAULT` to a local test corpus to enable the optional integration
test.
