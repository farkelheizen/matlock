from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

from matlock_ingest.config import load_config
from matlock_ingest.database import Database
from matlock_ingest.ingest import IngestionEngine
from matlock_audit.config import load_audit_config
from matlock_audit.engine import AuditEngine
from matlock_audit.heuristics import HeuristicScanner
from matlock_audit.hooks import Hooks, load_hooks
from matlock_audit.store import AuditStore
from tests.test_ingest import TestEmbedder

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SAFE_LLM_JSON = json.dumps(
    {"risk_score": 1, "llm_confidence_score": 2, "reason": "looks fine", "flagged_snippets": []}
)

VAULT = {
    "notes/safe.md": "# Safe\nJust a note about gardening.\n",
    "notes/server.md": f"# Server\n## Old Server Config\naws_access_key_id = {FAKE_AWS_KEY}\n",
}


class FakeAuditor:
    def __init__(self, reply: str | Callable[[str], str] = SAFE_LLM_JSON) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply(prompt) if callable(self.reply) else self.reply


def build_index(
    tmp_path: Path, files: dict[str, str] | None = None, audit_toml: str = ""
) -> Path:
    """Write a vault and config, ingest it with a fake embedder, return the config path."""
    vault = tmp_path / "vault"
    for name, content in (VAULT if files is None else files).items():
        target = vault / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    config_path = tmp_path / "matlock.toml"
    config_path.write_text(
        '[ingest]\nvault_path = "./vault"\ndatabase_path = "./index.sqlite3"\n'
        "[ingest.embedding]\ndimensions = 2\n" + audit_toml,
        encoding="utf-8",
    )
    config = load_config(config_path)
    with Database(config.database_path, config) as database:
        IngestionEngine(config, database, TestEmbedder()).scan()
    return config_path


def make_engine(
    config_path: Path,
    *,
    auditor: FakeAuditor | None = None,
    heuristics: bool = True,
    hooks: Hooks | None = None,
) -> AuditEngine:
    ingest_config, config = load_audit_config(config_path)
    store = AuditStore(ingest_config.database_path, ingest_config.busy_timeout_ms)
    return AuditEngine(
        store,
        config,
        hooks if hooks is not None else load_hooks(config),
        heuristics=HeuristicScanner(config.heuristic_plugins) if heuristics else None,
        auditor=auditor,
    )


def attributes(config_path: Path) -> dict[tuple[str, str], dict]:
    database_path = load_config(config_path).database_path
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT domain, sha256, attributes_json FROM content_attributes"
        ).fetchall()
    finally:
        connection.close()
    return {(d, s): json.loads(j) for d, s, j in rows}
