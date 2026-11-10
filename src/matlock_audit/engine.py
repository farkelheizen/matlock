from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import asdict, replace
from typing import Any

from . import __version__
from .heuristics import HeuristicScanner, mask
from .hooks import Hooks
from .llm import Auditor, parse_audit_response, render_prompt
from .models import Finding, PassReport, ScanItem, ScanResult, AuditConfig
from .store import AuditStore, utc_now

log = logging.getLogger("matlock_audit")

HEURISTIC_RISK = 8
FAILURE_RISK = 5
_MAX_REASON = 500


class _StageError(Exception):
    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(stage)
        self.stage = stage
        self.cause = cause


def _call(stage: str, function: Callable[..., Any], *args: Any) -> Any:
    try:
        return function(*args)
    except Exception as exc:
        raise _StageError(stage, exc) from exc


class AuditEngine:
    def __init__(
        self,
        store: AuditStore | None,
        config: AuditConfig,
        hooks: Hooks,
        *,
        heuristics: HeuristicScanner | None = None,
        auditor: Auditor | None = None,
        auditor_factory: Callable[[], Auditor] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.hooks = hooks
        self.heuristics = heuristics
        self._auditor = auditor
        self._auditor_factory = auditor_factory
        self.llm_enabled = auditor is not None or auditor_factory is not None
        self._prompt_digest = hashlib.sha256(config.prompt.encode("utf-8")).hexdigest()

    def _get_auditor(self) -> Auditor:
        if self._auditor is None:
            assert self._auditor_factory is not None
            self._auditor = self._auditor_factory()
        return self._auditor

    def _provenance(self) -> dict[str, Any]:
        info: dict[str, Any] = {"scanner_version": __version__}
        if self.llm_enabled:
            info["model_id"] = self.config.llm_model_id
            info["prompt_sha256"] = self._prompt_digest
        return info

    def _skipped(self, reason: str) -> ScanResult:
        return ScanResult(risk_score=0, llm_status="skipped", skip_reason=reason)

    def evaluate(self, item: ScanItem) -> ScanResult:
        try:
            return self._evaluate(item)
        except _StageError as failure:
            log.warning("%s failed: %s", failure.stage, type(failure.cause).__name__)
            return ScanResult(
                risk_score=FAILURE_RISK,
                llm_status="error",
                error=f"{failure.stage}: {type(failure.cause).__name__}",
            )

    def _evaluate(self, item: ScanItem) -> ScanResult:
        hooks, config = self.hooks, self.config
        if hooks.pre_scan is not None and not _call("hook:pre_scan", hooks.pre_scan, item):
            return self._skipped("pre_scan")

        text = item.content
        limit = config.max_chunk_chars if item.domain == "chunk" else config.max_document_chars
        if len(text) > limit:
            replacement = None
            if hooks.on_oversize is not None:
                replacement = _call("hook:on_oversize", hooks.on_oversize, item, limit)
                if replacement is not None and not isinstance(replacement, str):
                    raise _StageError("hook:on_oversize", TypeError("must return str or None"))
            if replacement is None:
                return self._skipped("oversize")
            text = replacement

        layers: list[str] = []
        findings: list[Finding] = []
        if self.heuristics is not None:
            layers.append("heuristic")
            findings.extend(_call("layer:heuristic", self.heuristics.scan, text))
            if hooks.heuristic_rules is not None:
                extra = _call("hook:heuristic_rules", hooks.heuristic_rules, item) or []
                if not all(isinstance(f, Finding) for f in extra):
                    raise _StageError("hook:heuristic_rules", TypeError("must return Findings"))
                findings.extend(extra)
        findings = [replace(f, preview=mask(f.preview)) if f.preview else f for f in findings]
        matched = bool(findings)

        llm_status, llm_risk, confidence, reason, snippets, error = (
            "disabled", 0, 0, "", (), None
        )
        if self.llm_enabled:
            layers.append("llm")
            template = config.prompt
            if hooks.prompt_context is not None:
                template = _call("hook:prompt_context", hooks.prompt_context, item, template)
                if not isinstance(template, str) or "{content}" not in template:
                    raise _StageError(
                        "hook:prompt_context", ValueError("must return a template with {content}")
                    )
            # Loading failures abort the pass instead of being stored as findings.
            auditor = self._get_auditor()
            try:
                raw = auditor.generate(render_prompt(template, text))
            except Exception as exc:
                log.warning("layer:llm failed: %s", type(exc).__name__)
                llm_status, llm_risk, error = "error", FAILURE_RISK, f"layer:llm: {type(exc).__name__}"
            else:
                try:
                    audit = parse_audit_response(raw)
                except Exception as exc:
                    llm_status = "parse_error"
                    llm_risk = FAILURE_RISK if matched else 0
                    error = f"parse: {type(exc).__name__}"
                else:
                    llm_status, llm_risk = "ok", audit.risk_score
                    confidence = audit.llm_confidence_score
                    reason = audit.reason[:_MAX_REASON]
                    snippets = tuple(mask(s) for s in audit.flagged_snippets)

        result = ScanResult(
            risk_score=max(llm_risk, HEURISTIC_RISK if matched else 0),
            regex_matches_found=matched,
            llm_confidence_score=confidence,
            reason=reason,
            flagged_snippets=snippets,
            heuristic_findings=tuple(findings),
            layers=tuple(layers),
            llm_status=llm_status,
            error=error,
        )
        if hooks.risk_policy is not None:
            score = _call("hook:risk_policy", hooks.risk_policy, item, result)
            if isinstance(score, bool) or not isinstance(score, int):
                raise _StageError("hook:risk_policy", TypeError("must return an int"))
            result = replace(result, risk_score=max(0, min(10, score)))
        return result

    def to_json(self, result: ScanResult) -> dict[str, Any]:
        data = asdict(result)
        data["flagged_snippets"] = list(result.flagged_snippets)
        data["heuristic_findings"] = [asdict(f) for f in result.heuristic_findings]
        data["layers"] = list(result.layers)
        data = {k: v for k, v in data.items() if v is not None}
        data["provenance"] = self._provenance()
        data["scanned_at"] = utc_now()
        return data

    def run_pass(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        should_stop: Callable[[], bool] = lambda: False,
        on_item: Callable[[ScanItem, ScanResult], None] | None = None,
    ) -> PassReport:
        assert self.store is not None, "run_pass requires a database store"
        report = PassReport(dry_run=dry_run)
        remaining = limit
        for domain in self.config.domains:
            while not should_stop() and (remaining is None or remaining > 0):
                if dry_run:
                    size = remaining  # one read: nothing is written, so batches would repeat
                else:
                    size = self.config.batch_size
                    if remaining is not None:
                        size = min(size, remaining)
                items = self.store.unscanned(domain, size)
                if not items:
                    break
                rows: list[tuple[str, str, dict[str, Any]]] = []
                done: list[tuple[ScanItem, ScanResult]] = []
                for item in items:
                    if should_stop():
                        break
                    result = self.evaluate(item)
                    done.append((item, result))
                    rows.append((item.domain, item.sha256, self.to_json(result)))
                if not dry_run and rows:
                    self.store.insert_results(rows, self.config.attribute_key)
                for item, result in done:
                    report.scanned += 1
                    report.skipped += result.llm_status == "skipped"
                    report.errors += result.llm_status == "error"
                    report.flagged += result.regex_matches_found or result.risk_score >= 5
                    if on_item is not None:
                        on_item(item, result)
                    if not dry_run and self.hooks.post_scan is not None:
                        try:
                            self.hooks.post_scan(item, result)
                        except Exception as exc:
                            log.warning("hook:post_scan failed: %s", type(exc).__name__)
                if remaining is not None:
                    remaining -= len(done)
                if dry_run:
                    break
        return report
