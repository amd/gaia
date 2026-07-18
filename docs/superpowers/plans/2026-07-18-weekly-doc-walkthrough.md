# Weekly Doc Walkthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `.github/workflows/claude-weekly-doc-walkthrough.yml`, an execution-based
sibling to the static `claude-weekly-audit.yml`, that acts as a real user/developer
working through GAIA's documentation on the self-hosted Windows/STX runner and files
findings where reality diverges from the docs.

**Architecture:** One GH Actions workflow, matrix over discovered doc files
(`max-parallel: 1`). Each matrix entry: isolated venv + isolated `GAIA_HOME` + a
dedicated-port Lemonade instance, a Sonnet **executor** step that walks the guide's
commands and captures raw output, an Opus **judge** step that independently verifies the
transcript against the doc, writing `findings-walkthrough-<slug>.json`. A synthesis job
(same conventions as `claude-weekly-audit.yml`: label scheme, dedup-key marker, child
issues for 🔴/🟠 only) files one `Doc walkthrough — <run_id>` parent issue.

**Tech Stack:** GitHub Actions (YAML), PowerShell (Windows runner steps),
`anthropics/claude-code-action`, `gaia` CLI, Lemonade Server.

## Global Constraints

- Runner: `[self-hosted, Windows, stx]` for every step that installs/runs GAIA or Lemonade.
- Never call `cleanup-lemonade.ps1` or force-kill any process this run didn't start.
- Never install GAIA editable (`-e`) or install any `hub/agents/python/*` package
  alongside core — the fresh-venv, real-user-install property is load-bearing (#2260).
- Model-resolution-shaped checks (does an agent 404 on an uninstalled preferred model) use
  a targeted Python snippet against `AgentRegistry`, never a live CLI run against a
  model-catalog that might already be warmed by unrelated CI (#2261) — see design doc
  "Execution environment" item 3.
- Executor model: Sonnet. Judge model: `claude-opus-4-8` (matches `AUDIT_MODEL` in the
  static audit — single source of truth, defined once as a workflow-level `env`).
- Severity scale 🔴/🟠/🟡 (no green), `dedup_key` format
  `walkthrough:<doc-path>:<step-heading>`, labels `weekly-audit` / `audit-wontfix`,
  🟡 findings roll into the parent only (no child issue) — same conventions as
  `claude-weekly-audit.yml` (`weekly-audit-patterns` skill).
- No Claude attribution anywhere in generated issue text.
- A finding must reproduce (retry once) before being written to the findings JSON.
- Every out-of-scope step (missing creds/hardware/platform) is reported explicitly as
  `"requires <X> — not verifiable in this environment"`, never silently skipped.

---

### Task 1: Design + non-goals docs (already complete)

**Files:**
- Created: `docs/plans/weekly-doc-walkthrough-audit.md`
- Modified: `docs/plans/weekly-claude-audit.md` (non-goals bullet now points at the
  sibling workflow)

- [x] Written and cross-referenced in this session.

### Task 2: Doc discovery + skip helper script

**Files:**
- Create: `scripts/audit/discover_walkthrough_docs.py`
- Test: manual run (see Step 3) — this is a standalone CLI helper, not a pytest unit; it
  has no GAIA runtime dependency (stdlib only) so it can be exercised directly.

**Interfaces:**
- Produces: a JSON array on stdout, `[{"path": "docs/guides/chat.mdx", "slug": "guides-chat"}, ...]`,
  consumed by Task 3's `preflight` job as the matrix input (`fromJson`).

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Discover docs in scope for the weekly doc walkthrough.

Emits a JSON array of {"path": ..., "slug": ...} for every doc the walkthrough
should walk. Glob-based (not a hardcoded list) so a new guide is picked up
automatically -- same extensibility principle as claude-weekly-audit.yml's
dimension matrix. stdlib only: this runs before any venv exists.
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Explicit exclude list for docs that are structurally out of scope for a
# CLI walkthrough (not "hard to test" -- those still get walked with a
# per-step "not verifiable" flag; these have no command-line surface at all).
EXCLUDE = {
    "docs/guides/agent-ui.mdx",  # stretch goal: needs a browser driver, see design doc
}

GLOBS = [
    "docs/guides/*.mdx",
    "docs/quickstart.mdx",
    "docs/setup.mdx",
    "docs/reference/cli.mdx",
]


def slugify(rel_path: str) -> str:
    stem = re.sub(r"\.mdx?$", "", rel_path)
    return re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()


def discover() -> list[dict]:
    seen = set()
    docs = []
    for pattern in GLOBS:
        for match in sorted(REPO_ROOT.glob(pattern)):
            rel = match.relative_to(REPO_ROOT).as_posix()
            if rel in EXCLUDE or rel in seen:
                continue
            seen.add(rel)
            docs.append({"path": rel, "slug": slugify(rel)})
    return docs


def main() -> int:
    docs = discover()
    if not docs:
        print("no docs discovered -- EXCLUDE list or GLOBS is misconfigured", file=sys.stderr)
        return 1
    print(json.dumps(docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/audit/discover_walkthrough_docs.py
```

- [ ] **Step 3: Run it and verify real output**

Run: `python3 scripts/audit/discover_walkthrough_docs.py | python3 -m json.tool | head -20`
Expected: a JSON array whose entries include `docs/guides/chat.mdx`,
`docs/quickstart.mdx`, `docs/reference/cli.mdx`, and do NOT include
`docs/guides/agent-ui.mdx`. Confirm the count is sane (`... | python3 -c
"import json,sys; print(len(json.load(sys.stdin)))"` — expect roughly the number of files
`ls docs/guides/*.mdx docs/quickstart.mdx docs/setup.mdx docs/reference/cli.mdx | wc -l`
reports, minus 1 for the excluded Agent UI guide).

- [ ] **Step 4: Commit**

```bash
git add scripts/audit/discover_walkthrough_docs.py
git commit -m "feat(ci): doc-discovery helper for the weekly doc walkthrough"
```

### Task 3: The workflow file — preflight, matrix skeleton, environment isolation

**Files:**
- Create: `.github/workflows/claude-weekly-doc-walkthrough.yml`

**Interfaces:**
- Consumes: `scripts/audit/discover_walkthrough_docs.py` (Task 2) for the matrix.
- Produces: per-matrix-entry `findings-walkthrough-<slug>.json` artifacts, consumed by
  Task 5's synthesis job.

- [ ] **Step 1: Write the header comment + triggers + concurrency + env**

```yaml
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Execution-based sibling to claude-weekly-audit.yml (see docs/plans/
# weekly-doc-walkthrough-audit.md and docs/plans/weekly-claude-audit.md's
# "Non-goals"). That workflow is deliberately static (read-only, never installs
# or runs repo code) -- this one exists because that non-goal has a real blind
# spot: #2260 (gaia chat ImportErrors on a plain PyPI install) and #2261 (seven
# hub agents silently fall back to an uninstalled 35B model) are both only
# observable by actually running GAIA from a real user's initial state, never
# by reading source. This workflow acts as that user: one matrix job per doc
# guide, each walking the guide's commands for real on the self-hosted
# Windows/STX runner, in a fresh venv + isolated config + a dedicated-port
# Lemonade instance -- never the runner's shared, CI-warmed install (see
# "Execution environment" in the design doc for why that distinction matters).
#
# STANDALONE from claude-weekly-audit.yml on purpose: different runner
# (self-hosted Windows vs. ubuntu-latest), different cost/runtime profile
# (hours, not minutes -- deliberate; see design doc "Cadence"), different
# failure modes (a live command can hang). Isolating them means a stuck
# walkthrough never delays the fast static audit's triage issue. Shares
# vocabulary with it (severity scale, dedup-key pattern, weekly-audit label,
# bug-label auto-fix promotion) but files its OWN parent issue.
#
# MODEL SPLIT: Sonnet executes (cheap, high tool-call volume, grinding
# sequential work); Opus (WALKTHROUGH_JUDGE_MODEL) judges (low volume -- one
# pass per guide -- and never rubber-stamps the executor's own summary, same
# principle the gaia-testing skill states explicitly). See design doc "Job
# structure".
#
# PORT ISOLATION: runs its own Lemonade Server on a dedicated, non-13305 port
# so it never collides with the six other workflows issue #2122 documents
# fighting over the shared runner's Lemonade instance -- sidesteps that
# contention by construction, not by coordination. NEEDS LIVE-RUNNER
# VERIFICATION (flagged in the design doc): whether a second Lemonade Server
# instance can run concurrently on this box and what it does for model-cache
# visibility. Model-*resolution* checks (the #2261 shape) do not depend on the
# answer -- they use a targeted Python snippet against AgentRegistry instead
# of a live model catalog; see Task 4.
#
# NOT VALIDATED END-TO-END AT AUTHORING TIME: this workflow was written and
# reviewed without access to the self-hosted runner. Run it once via
# workflow_dispatch with doc_filter scoped to a single guide before trusting
# the full weekly schedule.

name: Claude Weekly Doc Walkthrough

on:
  schedule:
    # 12:00 UTC every Monday -- offset 6h from claude-weekly-audit.yml's 06:00
    # UTC run so the two don't compete for runner time or review attention.
    - cron: "0 12 * * 1"
  workflow_dispatch:
    inputs:
      doc_filter:
        description: "Glob to scope this run to one or a few docs (e.g. docs/guides/chat.mdx). Empty = all discovered docs."
        default: ""

permissions:
  contents: read

# Own group -- deliberately NOT the eval workflows' `lemonade-eval` group.
# This workflow runs its own Lemonade instance on a dedicated port (see header),
# so it does not need to serialize against workflows using the shared 13305
# instance; it only needs to serialize against ITSELF across scheduled runs.
concurrency:
  group: claude-weekly-doc-walkthrough
  cancel-in-progress: false

env:
  WALKTHROUGH_EXECUTOR_MODEL: claude-sonnet-4-6
  WALKTHROUGH_JUDGE_MODEL: claude-opus-4-8
  # Dedicated port for this workflow's own Lemonade instance -- never touches
  # the shared 13305 port the other self-hosted-runner workflows use.
  WALKTHROUGH_LEMONADE_PORT: 13405

jobs:
```

- [ ] **Step 2: Write the `discover` job**

```yaml
  discover:
    if: github.repository == 'amd/gaia'
    runs-on: ubuntu-latest
    outputs:
      docs: ${{ steps.discover.outputs.docs }}
    steps:
      - uses: actions/checkout@v7

      - name: Discover in-scope docs
        id: discover
        env:
          DOC_FILTER: ${{ github.event.inputs.doc_filter }}
        run: |
          set -euo pipefail
          all_docs=$(python3 scripts/audit/discover_walkthrough_docs.py)
          if [ -n "$DOC_FILTER" ]; then
            filtered=$(python3 -c "
          import json, os, fnmatch
          docs = json.loads(os.environ['ALL_DOCS'])
          pattern = os.environ['DOC_FILTER']
          print(json.dumps([d for d in docs if fnmatch.fnmatch(d['path'], pattern)]))
          " )
            echo "docs=$filtered" >> "$GITHUB_OUTPUT"
            echo "Filtered to: $filtered"
          else
            echo "docs=$all_docs" >> "$GITHUB_OUTPUT"
            echo "All in-scope docs: $all_docs"
          fi
        env:
          ALL_DOCS: ${{ steps.discover.outputs.docs }}
          DOC_FILTER: ${{ github.event.inputs.doc_filter }}
```

Note the two-pass env reference (the `ALL_DOCS` step output isn't set until the `id:
discover` step's `run:` block finishes) — restructure as two steps in the actual file if
`actions/github-script` proves cleaner than shelling out to Python twice; validate with
`act` or a `workflow_dispatch` dry run before relying on it (this is exactly the kind of
detail Step 3 of Task 6 exists to catch).

- [ ] **Step 3: Write the `walkthrough` matrix job skeleton (environment isolation only, no executor/judge yet)**

```yaml
  walkthrough:
    needs: discover
    if: github.repository == 'amd/gaia' && needs.discover.outputs.docs != '[]'
    runs-on: [self-hosted, Windows, stx]
    strategy:
      fail-fast: false
      max-parallel: 1
      matrix:
        doc: ${{ fromJson(needs.discover.outputs.docs) }}
    timeout-minutes: 90  # one guide's ceiling; the workflow overall may run for hours across the matrix
    steps:
      - uses: actions/checkout@v7

      - name: Verify clean start state
        shell: powershell
        run: |
          $runDir = "$env:RUNNER_TEMP\walkthrough-${{ matrix.doc.slug }}"
          if (Test-Path $runDir) {
            Write-Host "Stale run dir from a previous failed run -- removing: $runDir"
            Remove-Item -Recurse -Force $runDir
          }
          New-Item -ItemType Directory -Path $runDir | Out-Null
          echo "RUN_DIR=$runDir" >> $env:GITHUB_ENV
          echo "GAIA_HOME=$runDir\gaia_home" >> $env:GITHUB_ENV

      - name: Fresh venv, real-user install (no -e, no hub packages)
        shell: powershell
        run: |
          python -m venv "$env:RUN_DIR\venv"
          & "$env:RUN_DIR\venv\Scripts\python.exe" -m pip install --upgrade pip build
          & "$env:RUN_DIR\venv\Scripts\python.exe" -m build --wheel --outdir "$env:RUN_DIR\dist" .
          $wheel = Get-ChildItem "$env:RUN_DIR\dist\amd_gaia-*.whl" | Select-Object -First 1
          & "$env:RUN_DIR\venv\Scripts\pip.exe" install $wheel.FullName
          & "$env:RUN_DIR\venv\Scripts\gaia.exe" --version

      - name: Start an isolated Lemonade instance on the dedicated port
        shell: powershell
        run: |
          # Health-check first -- never assume the port is free, never force-kill
          # whatever might already be on it (this workflow owns WALKTHROUGH_LEMONADE_PORT
          # exclusively across its own runs, but a leftover process from a prior
          # crashed run of THIS workflow is a legitimate thing to clean up).
          $healthUrl = "http://localhost:$env:WALKTHROUGH_LEMONADE_PORT/api/v1/health"
          try {
            $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
              Write-Host "Dedicated-port Lemonade already healthy (leftover from a prior run) -- reusing it."
              exit 0
            }
          } catch {
            Write-Host "Dedicated port not yet serving -- starting a fresh instance."
          }
          Start-Process -FilePath "LemonadeServer.exe" -ArgumentList "serve --port $env:WALKTHROUGH_LEMONADE_PORT" -PassThru |
            ForEach-Object { $_.Id } | Out-File "$env:RUN_DIR\lemonade.pid"
          # Poll for health rather than a fixed sleep.
          $deadline = (Get-Date).AddSeconds(60)
          do {
            Start-Sleep -Seconds 2
            try {
              $resp = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
              if ($resp.StatusCode -eq 200) { break }
            } catch {}
          } while ((Get-Date) -lt $deadline)
```

- [ ] **Step 4: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/claude-weekly-doc-walkthrough.yml'))" `
Expected: no exception. (This only proves syntactic validity, not runner-side
correctness — Task 6 covers what can and can't be verified before a live run.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/claude-weekly-doc-walkthrough.yml
git commit -m "feat(ci): scaffold the weekly doc walkthrough workflow"
```

### Task 4: Executor + judge steps, model-resolution snippet checks

**Files:**
- Modify: `.github/workflows/claude-weekly-doc-walkthrough.yml` (append to the
  `walkthrough` job from Task 3)

**Interfaces:**
- Consumes: `RUN_DIR`, `GAIA_HOME`, `WALKTHROUGH_LEMONADE_PORT` env vars set in Task 3.
- Produces: `findings-walkthrough-${{ matrix.doc.slug }}.json` (same shape as
  `claude-weekly-audit.yml`'s findings, `dedup_key` namespaced `walkthrough:...`).

- [ ] **Step 1: Add the executor step**

```yaml
      - name: "Executor (Sonnet): walk ${{ matrix.doc.path }}"
        id: executor
        continue-on-error: true
        uses: anthropics/claude-code-action@e90deca47693f9457b72f2b53c17d7c445a87342  # v1.0.171
        with:
          anthropic_api_key: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            You are the EXECUTOR half of GAIA's weekly doc walkthrough. Your only job is
            to run commands and capture their raw output -- judging correctness is a
            SEPARATE step that reads your transcript independently. Do not editorialize
            or decide what's a bug; just execute faithfully and record everything.

            DOC UNDER TEST: ${{ matrix.doc.path }}
            ENVIRONMENT: a fresh venv is already active at $env:RUN_DIR\venv (GAIA
            installed from a built wheel, no -e install, no hub agent packages
            preinstalled -- this is deliberate, do not "fix" a missing hub package by
            installing it). GAIA_HOME is isolated at $env:GAIA_HOME. A Lemonade instance
            is running at http://localhost:${{ env.WALKTHROUGH_LEMONADE_PORT }} -- point
            any Lemonade-aware command at that port, never the default 13305.

            ## What to do
            1. Read `${{ matrix.doc.path }}` in full.
            2. Extract every command a reader is expected to actually run, in the order
               the doc presents them (code fences, inline `gaia ...` examples).
            3. Run each command for real, using `$env:RUN_DIR\venv\Scripts\gaia.exe`
               explicitly (not a bare `gaia` that might resolve to something else on the
               runner). Capture stdout, stderr, exit code, and wall-clock time for each.
            4. **Stop the guide at its first failing step whose failure blocks later
               steps** (e.g. `gaia init` failing means don't attempt `gaia chat` after
               it) -- report the root step, don't manufacture cascading noise from steps
               whose precondition already failed. Steps that are independently runnable
               (separate subcommands not depending on the failed one) still get attempted.
            5. A step needing something this environment cannot provide (Jira/Atlassian
               credentials, a Google OAuth connector, a Blender install, a microphone, a
               non-Windows install path) is NOT attempted -- record it explicitly as
               `"requires <X> -- not verifiable in this environment"` and move to the next
               independently-runnable step.
            6. **Model-resolution checks are a special case -- do NOT drive them through
               a live end-to-end CLI run.** If the doc's guide involves an agent choosing
               a model when its preferred model isn't installed (this is exactly the
               #2261 bug shape), the runner's shared Lemonade cache may already have
               other models downloaded from unrelated CI and would silently defeat the
               check. Instead write and run a small, throwaway Python snippet using
               `$env:RUN_DIR\venv\Scripts\python.exe` that imports the relevant registry
               code and calls its model-resolution function with an EXPLICIT
               `available_models` list matching only what the doc's target `gaia init`
               profile installs (cross-reference `INIT_PROFILES` in
               `src/gaia/installer/init_command.py` for the real list) -- then assert the
               agent does not silently fall through to a different hardcoded model.
               Capture the snippet's full source and output as evidence.
            7. **A failing step must reproduce before you treat it as real.** Retry it
               once. If it passes the second time, note it as a flake (timing, network) in
               your transcript rather than a confirmed failure -- the judge decides what
               to do with that note, you do not decide it's a non-issue yourself.

            Write the full raw transcript (every command, its output, exit code, timing,
            and any retry) to `$env:RUN_DIR\transcript.md`, structured with one section per
            step in doc order. This transcript is the ONLY thing the judge step reads --
            anything not captured there does not exist for judging purposes.
          claude_args: |
            --max-turns 60
            --model ${{ env.WALKTHROUGH_EXECUTOR_MODEL }}
            --allowedTools Read,Grep,Glob,Bash
```

- [ ] **Step 2: Add the judge step**

```yaml
      - name: "Judge (Opus): verify ${{ matrix.doc.path }} against its transcript"
        id: judge
        if: always()
        continue-on-error: true
        uses: anthropics/claude-code-action@e90deca47693f9457b72f2b53c17d7c445a87342  # v1.0.171
        with:
          anthropic_api_key: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            You are the JUDGE half of GAIA's weekly doc walkthrough. Do not trust the
            executor's framing -- read its raw transcript and the doc yourself, and reach
            your own conclusion. This split exists because a single self-judging session
            is a known false-negative risk (see docs/plans/weekly-doc-walkthrough-audit.md
            "Job structure").

            DOC UNDER TEST: ${{ matrix.doc.path }}
            TRANSCRIPT: $env:RUN_DIR\transcript.md (if missing, the executor crashed before
            writing it -- file ONE 🟠 finding for that, dedup_key
            `walkthrough:${{ matrix.doc.path }}:executor-crash`, and stop)

            ## What to do
            1. Read `${{ matrix.doc.path }}` and `$env:RUN_DIR\transcript.md` in full.
            2. For each step in the transcript, check: does the actual captured output
               match what the doc claims will happen? A step marked "not verifiable in
               this environment" is not a finding by itself -- it's expected coverage,
               unless the doc presents that exact step as something every reader can run
               (in which case the doc itself needs a caveat -- that IS a finding).
            3. **`docs/reference/cli.mdx` gets an extra deterministic pass:** enumerate
               every flag from the transcript's `gaia -h` / subcommand `-h` output and
               every flag documented in the doc's tables, and diff them explicitly,
               flag by flag -- don't rely on a narrative skim for this file, a single
               dropped row in a long table is easy to miss that way.
            4. A step the executor noted as "reproduced as a flake" is NOT a finding --
               skip it. A step that failed and did NOT reproduce cleanly on retry (i.e.
               failed twice) IS eligible.
            5. Write findings to `findings-walkthrough-${{ matrix.doc.slug }}.json`
               (repo root), same shape `claude-weekly-audit.yml` uses:
               ```json
               {"findings": [
                 {
                   "severity": "🔴" | "🟠" | "🟡",
                   "path": "${{ matrix.doc.path }}",
                   "symbol": "the doc heading or step name -- NOT a line number",
                   "title": "one-line statement of the problem",
                   "why": "one sentence: the concrete impact / what breaks",
                   "evidence": "the exact transcript excerpt (command + captured output) proving the claim",
                   "auto_fixable": true | false,
                   "dedup_key": "walkthrough:${{ matrix.doc.path }}:<step-heading>"
                 }
               ]}
               ```
               Write `{"findings": []}` if nothing is wrong. Severity: 🔴 a default-path
               command in the doc fails outright; 🟠 the doc's claim about behavior is
               false but the underlying command doesn't crash; 🟡 a cosmetic/minor gap
               (a missing caveat, a stale example). `auto_fixable` = true only if the fix
               is small and locatable (~1-3 files, no design call).
          claude_args: |
            --max-turns 25
            --model ${{ env.WALKTHROUGH_JUDGE_MODEL }}
            --allowedTools Read,Grep,Glob,Bash
```

- [ ] **Step 3: Add cleanup + upload steps**

```yaml
      - name: Verify clean end state
        if: always()
        shell: powershell
        run: |
          if (Test-Path "$env:RUN_DIR\lemonade.pid") {
            $pid = Get-Content "$env:RUN_DIR\lemonade.pid"
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
          }
          Remove-Item -Recurse -Force $env:RUN_DIR -ErrorAction SilentlyContinue
          if (Test-Path $env:RUN_DIR) {
            Write-Host "::warning::Run dir did not clean up: $env:RUN_DIR"
          } else {
            Write-Host "Clean: run dir removed."
          }
          # Confirm this run never touched the shared 13305 instance's health.
          try {
            $resp = Invoke-WebRequest -Uri "http://localhost:13305/api/v1/health" -TimeoutSec 3 -ErrorAction Stop
            Write-Host "Shared Lemonade (13305) still healthy: $($resp.StatusCode)"
          } catch {
            Write-Host "Shared Lemonade (13305) not reachable or not running -- not this workflow's concern, just confirming we didn't kill it if it WAS up."
          }

      - name: Upload findings
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: findings-walkthrough-${{ matrix.doc.slug }}
          path: findings-walkthrough-${{ matrix.doc.slug }}.json
          if-no-files-found: warn
```

- [ ] **Step 4: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/claude-weekly-doc-walkthrough.yml'))"`
Expected: no exception.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/claude-weekly-doc-walkthrough.yml
git commit -m "feat(ci): executor/judge steps for the weekly doc walkthrough"
```

### Task 5: Synthesis job

**Files:**
- Modify: `.github/workflows/claude-weekly-doc-walkthrough.yml` (append the `synthesize`
  job)

**Interfaces:**
- Consumes: `findings-walkthrough-*.json` artifacts from Task 4.
- Produces: one `Doc walkthrough — <run_id>` GitHub issue, `weekly-audit`-labeled,
  cross-linking the previous walkthrough parent (never closing it) — same lifecycle rule
  as `claude-weekly-audit.yml`'s synthesis job.

- [ ] **Step 1: Write the `synthesize` job**, adapting `claude-weekly-audit.yml`'s
  synthesis prompt (dedup against `weekly-audit`/`audit-wontfix` keys, evidence gate,
  fixed-order parent body, cross-link not close) with these differences: title prefix
  `Doc walkthrough —` instead of `Weekly audit —`; no per-dimension sections (this
  workflow has one detection mechanism, not five) — group findings by doc path instead,
  most-severe first; no security carve-out (this workflow's prompts never instruct
  finding of security issues — out of scope, the static audit already owns that lens).

```yaml
  synthesize:
    needs: [discover, walkthrough]
    if: github.repository == 'amd/gaia' && !cancelled() && needs.discover.outputs.docs != '[]'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
    steps:
      - uses: actions/checkout@v7

      - name: Download all findings
        uses: actions/download-artifact@v8
        with:
          path: walkthrough-findings

      - name: Ensure labels exist
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh label create weekly-audit --color 5319e7 \
            --description "Proactive weekly Claude audit finding" \
            2>/dev/null && echo "created weekly-audit label" || echo "weekly-audit label already exists"
          gh label create audit-wontfix --color cccccc \
            --description "Accepted debt -- weekly audit will never re-file this finding" \
            2>/dev/null && echo "created audit-wontfix label" || echo "audit-wontfix label already exists"

      - name: Synthesize and file the triage issue
        id: claude
        continue-on-error: true
        uses: anthropics/claude-code-action@e90deca47693f9457b72f2b53c17d7c445a87342  # v1.0.171
        with:
          anthropic_api_key: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN == '' && secrets.ANTHROPIC_API_KEY || '' }}
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          prompt: |
            You are the synthesis step of GAIA's weekly DOC WALKTHROUGH (the
            execution-based sibling of the static weekly-audit -- see
            docs/plans/weekly-doc-walkthrough-audit.md). Each matrix job wrote a
            findings-walkthrough-<slug>.json. Dedupe, rank, and file the output.

            REPO: ${{ github.repository }}
            RUN LOG: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}

            ## First actions
            1. Read every `findings-walkthrough-*.json` under `walkthrough-findings/`.
               Missing/empty files are fine.
            2. Read `CLAUDE.md` -> "Issue Response Guidelines" for tone/format.
            3. Already-filed keys: `gh issue list --repo ${{ github.repository }} --label weekly-audit --state open --json number,title,body --limit 200`,
               collect `<!-- audit-key: KEY -->` markers.
            4. Suppressed-forever keys: `gh issue list --repo ${{ github.repository }} --label weekly-audit --label audit-wontfix --state all --json body --limit 200`.
            5. Previous parent to cross-link: the most recently created OPEN issue whose
               title starts `Doc walkthrough —` (NOT `Weekly audit —` -- that's the other
               workflow's parent, a different lineage; do not cross-link across workflows).

            ## Dedup + verify
            1. Drop any finding whose `dedup_key` is in either key set from step 3/4.
            2. Evidence gate: drop any 🔴/🟠 finding whose `evidence` doesn't actually
               substantiate its `title` -- spot-check by reading the transcript excerpt.
            3. If zero new findings remain, post NOTHING and exit cleanly.

            ## File child issues -- 🔴/🟠 only
            Same body format and rules as the static weekly audit's synthesis (no `bug`
            label, `<!-- audit-key: KEY -->` required, "Not acting on this? Close it with
            `audit-wontfix`" line, Auto-fixable line from the finding's field).

            ## File ONE parent issue
            Title: `Doc walkthrough — ${{ github.run_id }}`. Label `weekly-audit`.
            Group findings by DOC PATH (not by dimension -- this workflow has one
            mechanism), most-severe-first across the whole run, 🔴 before 🟠 before 🟡.
            Start with a one-line tally: `New this run: N (🔴 a · 🟠 b) · low
            (in-parent only): c · suppressed as dup/wontfix: d`.

            ## Cross-link the previous parent -- NEVER close it
            Same rule as the static audit: comment on the previous `Doc walkthrough —`
            parent found in step 5, leave it OPEN, never call `gh issue close` on a parent.

            ## Rules
            - No Claude attribution anywhere.
            - Do not open a PR, do not apply `bug` -- humans gate every code change.
            - Lead with the finding; one sentence per line (CLAUDE.md issue style).
          claude_args: |
            --max-turns 32
            --model ${{ env.WALKTHROUGH_JUDGE_MODEL }}
            --allowedTools Read,Grep,Glob,Bash
```

- [ ] **Step 2: Sanity-check the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/claude-weekly-doc-walkthrough.yml'))"`
Expected: no exception.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/claude-weekly-doc-walkthrough.yml
git commit -m "feat(ci): synthesis job for the weekly doc walkthrough"
```

### Task 6: Validate what can be validated without runner access, document what can't

**Files:**
- No new files; verification only.

- [ ] **Step 1: actionlint (if available) or a manual structural read-through**

Run: `actionlint .github/workflows/claude-weekly-doc-walkthrough.yml` if `actionlint` is
installed; otherwise `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/claude-weekly-doc-walkthrough.yml'))"`
plus a manual read comparing every `${{ }}` reference against a job/step output actually
defined earlier in the file (the `discover` job's two-pass env-var reference flagged in
Task 3 Step 2 is exactly the kind of bug this read-through exists to catch — resolve it
concretely here, don't leave the note from Task 3 unresolved).

Expected: no syntax errors; every referenced output/secret/env var traces to a real
definition.

- [ ] **Step 2: Confirm `docs/plans/weekly-doc-walkthrough-audit.md`'s "Open questions"
  section still accurately lists what remains unverified** (Lemonade dual-instance
  behavior, per-guide timeout tuning, the required first `workflow_dispatch` validation
  run) — update it if implementation changed any assumption from the design doc.

- [ ] **Step 3: State plainly, in the PR description, that this has NOT been run against
  the real runner** — a `workflow_dispatch` with `doc_filter: docs/quickstart.mdx` (or
  similar single-doc scope) is the required next step before trusting the weekly
  schedule, and per CLAUDE.md's "Commit Only When Bulletproof," this is disclosed rather
  than implied to be end-to-end verified.

### Task 7: Open the PR

**Files:** none (process step).

- [ ] **Step 1: Push the branch and open the PR** with a tight, why-first description
  (CLAUDE.md "PR Descriptions — Tight and Value-Focused"): lead with the #2260/#2261
  gap this closes, note the standalone-workflow + Sonnet/Opus split + port-isolation
  design decisions in one short paragraph each only if a reviewer needs them to evaluate
  the change, and an explicit test-plan checkbox for the required single-doc
  `workflow_dispatch` validation run (unchecked — it happens after merge, on the real
  runner, not from this session).
