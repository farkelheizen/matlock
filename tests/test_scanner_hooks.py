from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("detect_secrets")

from matlock2_ingest.config import ConfigurationError
from matlock2_scanner.config import load_scanner_config
from matlock2_scanner.hooks import Hooks, load_hooks
from matlock2_scanner.models import Finding
from tests.scanner_helpers import FakeAuditor, attributes, build_index, make_engine

HOOK_MODULE = '''
from matlock2_scanner.models import Finding

CALLS = []

def skip_journal(item):
    return not any(p.startswith("journal/") for p in item.source_paths)

def ticket_rule(item):
    return [Finding("Ticket", 1, "SEC-1234")] if "SEC-1234" in item.content else []

def add_hint(item, template):
    return "Extra hint.\\n" + template

def double(item, result):
    return result.risk_score * 2

def head(item, limit):
    return item.content[:limit]

def record(item, result):
    CALLS.append((item.domain, result.risk_score))

def wrong_signature(a, b, c, d):
    pass
'''


def write_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "scanner_hooks_under_test.py").write_text(HOOK_MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


def test_hooks_load_from_config_directory(tmp_path: Path) -> None:
    (tmp_path / "hooks_in_config_dir.py").write_text(HOOK_MODULE, encoding="utf-8")
    toml = '[secret_scanner.hooks]\npre_scan = "hooks_in_config_dir:skip_journal"\n'
    config_path = build_index(tmp_path, {"a.md": "# A\nx\n"}, toml)
    _, config = load_scanner_config(config_path)
    assert load_hooks(config).pre_scan is not None


@pytest.mark.parametrize(
    "spec",
    ["no_such_module_xyz:f", "scanner_hooks_under_test:missing", "scanner_hooks_under_test:wrong_signature"],
)
def test_bad_hooks_fail_at_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec: str
) -> None:
    write_module(tmp_path, monkeypatch)
    config_path = build_index(tmp_path, {"a.md": "# A\nx\n"}, f'[secret_scanner.hooks]\nrisk_policy = "{spec}"\n')
    _, config = load_scanner_config(config_path)
    with pytest.raises(ConfigurationError):
        load_hooks(config)


def test_pre_scan_hook_skips_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_module(tmp_path, monkeypatch)
    import scanner_hooks_under_test as module

    config = build_index(tmp_path, {"journal/j.md": "# J\nx\n", "notes/n.md": "# N\ny\n"})
    make_engine(config, auditor=None, hooks=Hooks(pre_scan=module.skip_journal)).run_pass()
    statuses = sorted(v["secret_scan"].get("skip_reason", "scanned") for v in attributes(config).values())
    assert statuses.count("pre_scan") == 2 and statuses.count("scanned") == 2


def test_rule_prompt_policy_and_post_hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_module(tmp_path, monkeypatch)
    import scanner_hooks_under_test as module

    module.CALLS.clear()
    config = build_index(tmp_path, {"a.md": "# A\nSee SEC-1234 for details.\n"})
    auditor = FakeAuditor()
    hooks = Hooks(
        heuristic_rules=module.ticket_rule,
        prompt_context=module.add_hint,
        risk_policy=module.double,
        post_scan=module.record,
    )
    make_engine(config, auditor=auditor, hooks=hooks).run_pass()
    chunk = next(v["secret_scan"] for (d, _), v in attributes(config).items() if d == "chunk")
    assert chunk["regex_matches_found"] and chunk["risk_score"] == 10  # max(1, 8) * 2 clamped
    assert any(f["type"] == "Ticket" for f in chunk["heuristic_findings"])
    assert all(p.startswith("Extra hint.") for p in auditor.prompts)
    assert len(module.CALLS) == 2


def test_hook_failures_become_error_results_not_aborts(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nx\n"})

    def broken(item):
        raise ValueError("secret detail")

    make_engine(config, auditor=None, hooks=Hooks(pre_scan=broken)).run_pass()
    value = next(iter(attributes(config).values()))["secret_scan"]
    assert value["llm_status"] == "error" and value["error"] == "hook:pre_scan: ValueError"
    assert "secret detail" not in str(value)


@pytest.mark.parametrize(
    "hooks",
    [
        Hooks(risk_policy=lambda item, result: "high"),
        Hooks(heuristic_rules=lambda item: ["not a finding"]),
        Hooks(prompt_context=lambda item, template: "no placeholder"),
    ],
)
def test_invalid_hook_results_are_errors(tmp_path: Path, hooks: Hooks) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nx\n"})
    make_engine(config, auditor=FakeAuditor(), hooks=hooks).run_pass()
    assert {v["secret_scan"]["llm_status"] for v in attributes(config).values()} == {"error"}


def test_post_scan_failure_does_not_affect_results(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nx\n"})

    def boom(item, result):
        raise RuntimeError

    report = make_engine(config, auditor=None, hooks=Hooks(post_scan=boom)).run_pass()
    assert report.scanned == 2 and report.errors == 0


def test_post_scan_not_called_on_dry_run(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nx\n"})
    calls: list[str] = []
    make_engine(
        config, auditor=None, hooks=Hooks(post_scan=lambda i, r: calls.append(i.sha256))
    ).run_pass(dry_run=True)
    assert calls == []


def test_finding_preview_from_hooks_is_masked(tmp_path: Path) -> None:
    config = build_index(tmp_path, {"a.md": "# A\nx\n"})
    hooks = Hooks(heuristic_rules=lambda item: [Finding("Custom", 1, "supersecretvalue")])
    make_engine(config, auditor=None, hooks=hooks).run_pass()
    assert "supersecretvalue" not in str(attributes(config))
