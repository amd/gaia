---
type: plan
source-issue: 1112
repo: amd/gaia
created: 2026-06-02
status: complete
work_type: config/infra
complexity: standard
tdd_required: false
suggested_team_size: 1
estimated_files_changed: 4
test_command: ".venv/bin/python -m pytest tests/unit/agents/email/ tests/unit/email/ tests/unit/connectors/ tests/unit/eval/ -q"
build_command: "uv venv && uv pip install -e .[dev,api]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1112-email-ci
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1112 — Email-agent CI: unit tests, nightly eval, C++ build

## Goal
Wire CI around everything the email-agent milestone built. Three workflows:

1. `test_email_agent_unit.yml` — PR + push, GitHub-hosted, fast (<~2 min): the
   email-agent / connectors / eval unit suite.
2. `test_email_agent_eval.yml` — nightly (`schedule`) + manual
   (`workflow_dispatch`), self-hosted AMD runner: drives the email-triage
   benchmark over `FakeGmailBackend` + the committed synthetic corpus, then reads
   the **committed report-mode threshold manifests** and logs/uploads the gate
   results. Report mode → never blocks the build (honest behavior; the accuracy
   bars cannot pass yet).
3. `build_cpp_email.yml` — **path-filtered scaffold**, dormant until
   `cpp/agents/email/**` lands (the C++ email agent does not exist yet).

## Stacked on #1277 (carries both threshold manifests + the eval harness)
Branch cut from `tmi/issue-1277-perf-metrics` (which is stacked on #1278).
Verified `BASE_OK` + `MANIFESTS_PRESENT` before any code:
- `tests/fixtures/email/quality_gate_thresholds.json` (FP<5%/FN<2%, `enforce:false`)
- `tests/fixtures/email/perf_gate_thresholds.json` (TTFT/tps/pipeline/peak-mem, `enforce:false`)

## The single committed thresholds source (no hardcoded thresholds in YAML)
The eval workflow reads the bars **only** through the harness's public loaders —
it never inlines a number:
- `gaia.eval.benchmark.load_default_quality_thresholds()` → reads
  `tests/fixtures/email/quality_gate_thresholds.json`
- `gaia.eval.benchmark.load_default_perf_thresholds()` → reads
  `tests/fixtures/email/perf_gate_thresholds.json`

CI keys off each gate's `should_fail` (= `enforce and not passed`). While the
manifests ship `enforce:false`, `should_fail` is always `false` → the gate
machinery runs, logs, and uploads, but the build is green. Flipping `enforce:true`
in the manifests (data, not code — owned by #1277/#1278/#1266) is what makes CI
block, once the accuracy work lands. This is documented at the top of the eval
workflow and below.

## Scope boundary (critical — build CI, do not fabricate code/gates)
- **Accuracy gates (categorization ≥85% #1266, phishing precision ≥90% #1271,
  draft-approval ≥70% #1269) are REPORT-ONLY this run.** The benchmark emits
  `category_accuracy` and a `phishing` confusion (with `precision`), so the eval
  workflow *reports* them — but there is **no committed accuracy-threshold
  manifest** (those are owned by #1266/#1271/#1269, not landed). Current corpus
  accuracy is ~0.40, so those bars cannot pass yet. The workflow logs the
  numbers; it does not invent a gate or hardcode a passing bar.
- **The C++ email agent does not exist** (`cpp/agents/email/` is absent). Per the
  repo's fail-loudly rule, do NOT build nonexistent code. `build_cpp_email.yml`
  is a **path-filtered scaffold** that triggers only on `cpp/agents/email/**`, so
  it stays dormant until the module ships, with a top-of-file comment explaining
  this. (The maintainer rescope on the issue also tracks the real C++ build under
  the C++ milestone / #1110 — this scaffold is the no-op placeholder, not that
  build.)

## Validated commands (run locally in this worktree)
- Unit job command (the exact `pytest` the workflow runs): **778 passed, 3
  skipped in ~9 s** with `.[dev,api]`. Comfortably under the <~2 min bar even
  with a cold install.
  - Required extras: `[dev]` (email + eval tests) **and** `[api]` (the
    `tests/unit/connectors/` FastAPI router tests import `fastapi`).
  - One local-only failure (`test_claude_judge.py::test_raises_on_missing_api_key`)
    is a developer-machine artifact: `gaia.eval.claude` calls `load_dotenv()` at
    import, which picks up a parent `~/src/amd/gaia/.env` that sets
    `ANTHROPIC_API_KEY` and re-injects it after `monkeypatch.delenv`. On a clean
    GitHub-hosted runner there is no parent `.env`; verified the test passes once
    the parent `.env` is removed.
- Gate-reader path (the inline Python the eval workflow runs): proven importable
  and correct offline — with every metric deliberately breaching, both gates
  report `should_fail=False` in report mode, so CI does not block.
- YAML validity: `python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"`.
- `actionlint` is not installed on this machine — fell back to YAML parse +
  embedded-Python `py_compile` of the gate-reader script.

## Lane boundary
- OWN: the three workflow files + this plan. Read-only on the manifests, the
  harness, and existing workflows.
- Do NOT modify the manifests (#1277/#1278), any `src/`, or other workflows.

## Files changed
- `.github/workflows/test_email_agent_unit.yml` (new)
- `.github/workflows/test_email_agent_eval.yml` (new)
- `.github/workflows/build_cpp_email.yml` (new, scaffold)
- `.claude/plans/issue-1112.md` (this file)

## AC coverage
- [x] `test_email_agent_unit.yml` — PR, GitHub-hosted, fast (<~2 min), email-agent
  unit tests (+ connectors + eval). REAL.
- [x] `test_email_agent_eval.yml` — nightly `schedule` + `workflow_dispatch`,
  self-hosted (`[self-hosted, lemonade-eval]`), FakeGmailBackend + committed
  corpus, runs the triage/quality/perf eval. REAL, **report mode**.
- [x] `build_cpp_email.yml` — PR, CMake build + mock tests of the C++ email agent.
  SCAFFOLD: path-filtered to `cpp/agents/email/**`, dormant until that lands.
- [x] Synthetic/sandbox mailboxes only (the committed `tests/fixtures/email`
  corpus + `FakeGmailBackend`; never a live mailbox).
- [~] Asserts gates: FP<5%/FN<2% + perf bars are read from the committed manifests
  and gated via `should_fail` (report mode → non-blocking now; `enforce:true`
  flips them on). Categorization ≥85% / phishing ≥90% / draft ≥70% are
  **report-only** pending #1266/#1271/#1269 (no committed accuracy manifest yet).
- [x] ONE committed thresholds source CI reads — the two manifests, via the
  harness loaders. No thresholds hardcoded in YAML.

## Open risks
- Self-hosted nightly cannot be exercised here (no Lemonade / no AMD hardware in
  this worktree). Validated the command + manifest resolution offline; the live
  run is post-hardware (the issue notes AMD hardware ~Jul 6).
- Draft-approval rate (#1269) is not yet emitted by the benchmark, so the eval
  workflow cannot report it until that lands; noted in the workflow.
