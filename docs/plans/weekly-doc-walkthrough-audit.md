# Weekly doc walkthrough — execution-based audit companion

Design record for `.github/workflows/claude-weekly-doc-walkthrough.yml`.

## Why this matters

`claude-weekly-audit.yml` (see `docs/plans/weekly-claude-audit.md`) is deliberately
**static** — five read-only Claude lenses that read and grep, never install or run repo
code. That non-goal was intentional, but it has a real blind spot: two bugs shipped that
are only observable by actually running GAIA from a real user's initial state, never by
reading source.

- **#2260** — `gaia chat` ImportErrors on a plain `pip install amd-gaia` because the
  `gaia-agent-chat` wheel it depends on was never published. Every existing CI path
  (`test_chat_agent.yml`) installs that hub package explicitly before testing, so nothing
  has ever exercised the environment a real PyPI user actually gets.
- **#2261** — seven hub agents silently fall back to a hardcoded 35B model when their
  only preferred model isn't installed, 404ing on any standard `gaia init` profile. The
  fallback is a three-hop chain (`registry.py` → `_build_create_kwargs` → the agent's
  own `__init__` default) that doesn't match any of the static audit's literal
  Fail-Loudly examples, and confirming it requires knowing which models a profile
  actually installs — external knowledge a static read can't easily reach.

This workflow is the execution-based counterpart: it acts as a real user/developer
working through GAIA's documentation, on real hardware, and reports where reality
diverges from the docs — a class of bug static analysis structurally cannot see.

**This supersedes the "no Lemonade-dependent checks" non-goal** in
`docs/plans/weekly-claude-audit.md` — that non-goal still holds for the *static* audit;
this sibling workflow is where execution-based verification now lives.

## Relationship to the static audit

A **standalone sibling workflow**, not a 6th dimension of `claude-weekly-audit.yml`:

- Different runner (`[self-hosted, Windows, stx]` vs. the static audit's `ubuntu-latest`).
- Different cost/runtime profile (hours, not minutes — deliberately; see Cadence).
- Different failure modes (can queue behind other work on a shared runner; a live command
  can hang or time out).

Isolating them means a slow or stuck walkthrough never delays or destabilizes the fast
static lenses' weekly triage issue. The two workflows share vocabulary — severity scale
(🔴 high · 🟠 medium · 🟡 low, no green), the `weekly-audit` label, the dedup-key pattern,
`bug` → auto-fix promotion, `audit-wontfix` permanent suppression — but each files its
**own** parent triage issue (`Doc walkthrough — <run_id>` vs. `Weekly audit — <mode> —
<run_id>`), because they are different detection mechanisms surfacing potentially
different root causes.

## Cadence & runtime

Weekly, same day as the static audit but offset (static audit: Monday 06:00 UTC; this
workflow: Monday 12:00 UTC) so the two don't compete for review attention or runner time
in the same window. **Deliberately allowed to run for hours** — this is the one weekly
deep pass, not a per-PR check, and thoroughness is the explicit goal over speed.
`workflow_dispatch` accepts an optional `doc_filter` glob input so a single guide can be
re-run in isolation (for validating the workflow itself, or re-checking one finding).

## Scope

Every file under `docs/guides/*.mdx`, plus `docs/quickstart.mdx`, `docs/setup.mdx`, and
`docs/reference/cli.mdx` — discovered by a **runtime glob**, not a hardcoded list, so a
new guide is covered automatically (same extensibility principle the static audit uses
for its dimensions).

For a guide whose flow needs something this runner genuinely cannot provide — Jira/
Atlassian credentials, a Google OAuth connector, a Blender install, a microphone, a
non-Windows install path (`install.sh` vs. `install.ps1`) — every CI-feasible step still
gets walked (install, config validation, flag parsing, documented error messages), and
the infeasible step is reported explicitly: `"requires <X> — not verifiable in this
environment"`. It is never silently skipped or silently counted as passing.

**Stretch goal, explicitly not in v1:** browser-driven verification of `agent-ui.mdx` via
Playwright (`gaia chat --ui`). Flagged here as a known, stated gap rather than a silent
absence — CLI/install/API-surface guides ship first.

## Execution environment (the crux of the design)

Runner: `[self-hosted, Windows, stx]` — GAIA's existing hardware runner pool (NPU-capable,
already running `install-lemonade`-managed Lemonade Server for other scheduled workflows).

**Isolation requirements, and how each is met:**

1. **A real user's install, not a dev checkout.** Fresh Python venv per run; GAIA
   installed via `pip install` against a **built wheel** — never `pip install -e
   .[dev,...]` and never an explicit hub-agent-package install alongside it. Existing CI
   (`test_chat_agent.yml`) always installs `hub/agents/python/chat` explicitly before
   testing ChatAgent, so this environment is genuinely novel — it is what would have
   caught #2260.
2. **A real user's config, not accumulated runner state.** `GAIA_HOME` set to a per-run
   temp directory (GAIA already supports this override — `installer/uninstall_command.py`
   reads `GAIA_HOME`). A guide's `gaia init` must not see, or leave behind, another run's
   config or model registry.
3. **A real user's model set, not this runner's accumulated cache.** This is the one
   confirmed, non-hypothetical risk in the design: `test_agent_behavior_e2e.yml` already
   pulls `Qwen3.5-35B-A3B-GGUF` onto this exact runner pool. If a guide's model-resolution
   behavior were checked by pointing a live CLI at the runner's shared, kitchen-sink
   Lemonade install, the 35B model being already-cached would silently defeat the exact
   check meant to catch #2261-shaped bugs — the runner would no longer resemble a fresh
   `gaia init --profile npu` machine.

   **Resolution:** model-*resolution*-shaped checks (does this agent 404 when its
   preferred model isn't installed) are verified with a **targeted Python snippet**, not
   a live end-to-end CLI run — construct `AgentRegistry.resolve_model()` (or the
   equivalent registry call) with an explicit `available_models` list matching exactly
   what the profile under test installs, and assert the agent doesn't silently fall
   through to a different hardcoded model. This sidesteps the shared-cache problem by
   construction: the check never depends on what happens to be downloaded on this box.
   Guides that need to verify **inference actually responds** (not "is this model
   installed") are unaffected by this and can use a live Lemonade instance normally,
   since the shared cache's contents are irrelevant to that question.
4. **No collision with the other workflows already fighting over the shared runner.**
   Issue #2122 (open, unresolved) documents six workflows force-killing port 13305 out
   from under each other via `cleanup-lemonade.ps1`. Rather than depending on #2122 being
   fixed first, this workflow **runs its own Lemonade Server instance on a dedicated,
   non-13305 port**, entirely separate from the shared instance the other workflows use —
   it sidesteps the contention by construction instead of by coordination. **Needs
   live-runner verification**, flagged honestly: whether two Lemonade Server instances can
   run concurrently on one Windows box (different ports), and whether a second instance
   defaults to a separate model-cache path or shares the global one. If they must share a
   cache path, that's still fine for the inference-response checks (item 3's snippet
   approach already covers the resolution-logic checks independent of cache contents).
5. **Cooperative, never destructive.** Never calls `cleanup-lemonade.ps1` or any
   force-kill path. Health-checks its own port before use; if occupied by a leftover
   process from a prior failed run, cleans up only what it started, never another
   workflow's.
6. **Clean start, clean end — verified, not assumed.** Before a run: confirm no stray
   process/venv/temp-dir survives from a previous run. After a run (success or failure):
   confirm no stray processes remain, the per-run temp dir is gone, and — if it touched
   anything shared — that shared state is unaffected. Concrete checks (`Get-Process`,
   directory-exists, a health probe), mirroring `gaia-testing`'s Phase 7 discipline.

## Job structure

One matrix entry per discovered doc file, `max-parallel: 1` — the same one-model-slot-at-
a-time discipline the eval workflows already use (CLAUDE.md: "Run agent evals SERIALLY").
This also gives natural checkpointing for an hours-long run: one guide hanging or timing
out doesn't lose the findings already captured from guides that finished first.

Each matrix entry:

1. **Environment setup** — venv, isolated `GAIA_HOME`, dedicated-port Lemonade instance
   (per Execution environment above).
2. **Executor pass — model: Sonnet.** Cheap and well-suited to high tool-call-volume,
   grinding, sequential work (mirrors the `gaia-testing` skill's own executor/judge model
   split: strongest model plans+judges, a faster model executes). Walks the guide's
   commands in the order a copy-pasting user would hit them, capturing raw stdout/stderr/
   exit code/timing per step. **Stops a guide at its first failing step** — if step 2
   depends on step 1 and step 1 failed, steps 3+ are not attempted; report the root cause,
   not five cascading symptoms of it.
3. **Judge pass — model: Opus** (matches the static audit's `AUDIT_MODEL`). Low volume —
   one pass per guide — so the cost difference against Sonnet here is small, and this is
   the piece worth paying for: it reviews the executor's **raw captured transcript**
   independently against the doc's literal claims. It does not rubber-stamp the executor's
   own summary — same principle `gaia-testing` states explicitly ("the executor's report
   is a claim, never trusted on its face"), applied here because a single self-judging
   session is exactly the kind of false-negative risk that would quietly erode trust in
   this workflow the way a false positive would.
4. **Deterministic layer for `cli.mdx` specifically.** In addition to the judge's
   narrative read, the judge is instructed to explicitly enumerate every flag from
   `gaia -h` / each subcommand's `-h` output and from the doc's flag tables and diff them
   line by line — narrative judgment alone is prone to missing a single dropped row in a
   long table. A follow-up hardening (out of scope for v1) would replace this with a real
   `argparse`-introspecting script; flagged here rather than silently deferred.
5. **Reproduce before filing.** A failing step is retried once before being treated as a
   confirmed finding — a shared, contended runner will have real transient failures
   (network blip, a slow model load), and one bad run is a flake, not a bug. Matches the
   static audit's "verify before you report" precision rule.
6. **Output** — `findings-walkthrough-<doc-slug>.json`, same shape as the static audit's
   findings (`severity`/`path`/`symbol`/`title`/`why`/`evidence`/`auto_fixable`/
   `dedup_key`), with `dedup_key` namespaced `walkthrough:<doc-path>:<step-heading>` —
   a different namespace from the static lenses' `<dimension>:<path>:<symbol>`, so the two
   mechanisms won't automatically cross-dedupe a shared root cause. Accepted as a known
   gap for v1: a maintainer fixing either finding closes the underlying bug, which makes
   the duplicate moot going forward.

## Synthesis

A separate synthesis job (same repo, `issues: write` only there, matching the static
audit's least-privilege pattern) collects every `findings-walkthrough-*.json`, dedupes
against already-open `weekly-audit`-labeled issues via the same `<!-- audit-key: KEY -->`
marker convention, rolls 🟡 (low) findings into the parent issue only (no child issue —
reuses the static audit's already-learned churn-control rule: its first deep run filed 19
children, ~13 low-value), files 🔴/🟠 findings as child issues, and posts one parent
`Doc walkthrough — <run_id>` issue labeled `weekly-audit`. Cross-links (never closes) the
previous walkthrough parent — the static audit's own postmortem (#2010: an earlier
auto-close silently hid 18 unaddressed children) is the reason this rule exists; it
applies identically here.

## Non-goals (v1)

- Browser-driven Agent UI verification (Playwright) — stated stretch goal, not silently
  dropped.
- macOS/Linux install-path verification — this runner is Windows-only; reported as
  unverifiable here, never claimed as covered.
- Jira / Blender / Google-OAuth-gated flows — reported as unverifiable without those
  credentials/software present, per-step, not skipped wholesale.
- A real `argparse`-introspecting deterministic flag-diff tool for `cli.mdx` (v1 uses an
  instructed-but-still-LLM enumeration/diff as an interim hardening).

## Open questions carried into implementation (stated, not hidden)

Implemented in `.github/workflows/claude-weekly-doc-walkthrough.yml`. Syntactically
validated with `actionlint` (clean) and `scripts/audit/discover_walkthrough_docs.py`
(run directly — discovers 28 docs, correctly excludes `agent-ui.mdx`). **Not** validated
end-to-end against the real runner — that requires items below, plus a live
`workflow_dispatch`.

1. Whether two Lemonade Server instances can coexist on one Windows box on different
   ports, and whether the second defaults to an isolated or shared model-cache path —
   needs verification on the real runner. Implemented as a dedicated-port instance
   (`WALKTHROUGH_LEMONADE_PORT: 13405`, separate from the shared 13305); the design does
   not depend on the answer being "isolated" (see Execution environment, item 3).
2. **New, found during implementation:** which shell `claude-code-action`'s Bash tool
   actually invokes on this Windows runner (PowerShell vs. bash/git-bash) was not
   confirmed at authoring time. The executor/judge prompts do not hardcode a syntax —
   they instruct Claude to detect its own shell and adapt — but this is a workaround for
   an unknown, not a resolution. Confirm on the first live run and hardcode the correct
   syntax once known (removes a class of avoidable flakiness).
3. Exact per-guide job timeout (implemented: 90 minutes) may need tuning once real
   command timings from a live run are known.
4. First live `workflow_dispatch` validation run (scoped to one guide via `doc_filter`,
   e.g. `docs/quickstart.mdx`) is required before enabling the full weekly schedule with
   confidence — this design was authored and reviewed without runner access, and is not
   claimed to be validated end-to-end.
