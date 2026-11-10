# Secret scanner example

[`examples/secret-scanner/`](../examples/secret-scanner/) is a small vault of
**fake** secrets plus a configuration that uses every scanner hook and a custom
LLM prompt. See the [scanner guide](secret-scanner.md) for the concepts.

## Contents

| File | Role |
|---|---|
| `matlock2.toml` | Ingest settings, low size limits, prompt file, and all hooks |
| `prompt.txt` | Custom LLM prompt for a team's private notes |
| `secret_scan_hooks.py` | The ingest metadata hook and six scanner hooks |
| `vault/notes/old-server-config.md` | A risky "Old Server Config" chunk next to clean chunks |
| `vault/notes/team-contacts.md` | Fake personal data, mainly for the LLM layer |
| `vault/notes/runbook.md` | An internal hostname |
| `vault/notes/long-note.md` | Long content with a secret at the end |
| `vault/public/announcement.md` | Public content that is skipped |
| `vault/restricted/rotation-plan.md` | A password in a restricted document |

## Hooks used

| Hook | What the example does |
|---|---|
| ingest metadata (`sensitivity_from_path`) | Labels documents `public`, `restricted`, or `internal` from their top-level folder |
| `pre_scan` (`skip_public`) | Skips content that only appears in public documents |
| `heuristic_rules` (`internal_hostnames`) | Flags `*.corp.example` hostnames |
| `prompt_context` (`add_sensitivity_context`) | Tells the model the sensitivity of the source documents |
| `risk_policy` (`weight_by_sensitivity`) | Adds 2 to risks of 3 or more in restricted documents |
| `on_oversize` (`head_and_tail`) | Scans the start and end of over-limit content |
| `post_scan` (`write_report`) | Appends content-free lines for risky items to `.matlock2/secret-scan-report.jsonl` |

## Run it

Use the scanner extras and run from the example directory (the report path is
relative to it):

```bash
pip install -e ".[scanner]"
cd examples/secret-scanner
matlock2 --config matlock2.toml ingest
matlock2-scanner --config matlock2.toml scan --no-llm
```

`--no-llm` needs no model download. For the LLM layer too, install
`".[scanner-llm]"` and omit the flag; the first run downloads the ungated
`Qwen/Qwen2.5-1.5B-Instruct` (about 3 GB), which is also the scanner's default.
A gated model such as `meta-llama/Llama-3.2-1B-Instruct` needs approved Hugging
Face access and `huggingface-cli login`, otherwise loading fails with a 401 error.

Expected output of the heuristic-only pass is a line like
`Scan complete: scanned=..., skipped=..., flagged=..., errors=0`. A second run
reports `scanned=0`. To see findings:

```sql
SELECT COALESCE(c.source_path, d.path) AS path, a.domain,
       json_extract(a.attributes_json, '$.secret_scan.risk_score') AS risk,
       json_extract(a.attributes_json, '$.secret_scan.skip_reason') AS skipped,
       json_extract(a.attributes_json, '$.secret_scan.heuristic_findings[0].type') AS first_finding
FROM content_attributes a
LEFT JOIN chunks c ON a.domain = 'chunk' AND c.content_sha256 = a.sha256
LEFT JOIN source_documents d ON a.domain = 'document' AND d.body_sha256 = a.sha256
ORDER BY risk DESC;
```

Expect the "Old Server Config" chunk and the restricted rotation chunk (risk
10) near the top, the runbook chunk flagged for its hostname, public content
skipped by `pre_scan`, and sibling chunks of the server note at 0. To reset,
delete `examples/secret-scanner/.matlock2/`.
