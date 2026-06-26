---
name: "adding-eval-scorecard"
description: "Adopt the per-agent eval scorecard for a GAIA hub agent: write the harness→payload adapter, run the eval to produce a REAL scorecard, link + surface it from the agent's README, wire the release gate, and (for a new agent) generalize the format. Use when asked to 'add a scorecard', 'adopt the eval scorecard', 'generate the scorecard for <agent>', or wire scorecard CI for an agent. Builds on docs/reference/eval-scorecard.mdx and the email agent reference adapter."
---

# Adding an Eval Scorecard to a GAIA Agent

Adopt the release **eval scorecard** ([`docs/reference/eval-scorecard.mdx`](../../../docs/reference/eval-scorecard.mdx)) for one hub agent. The system is `harness → result payload → generator → scorecard`, with a standalone presence+regression release gate. The **email agent is the reference implementation** — mirror it.

**Core modules (do not modify; reuse):**
- `src/gaia/eval/release_scorecard.py` — `ResultPayload`, `compute_aggregate`, `render_scorecard`, `write_scorecard`, `validate_scorecard`, `carry_forward`. Harness-agnostic (stdlib + PyYAML only).
- `src/gaia/eval/scorecard_gate.py` — the standalone gate (`python -m gaia.eval.scorecard_gate`).
- Reference adapter: `hub/agents/python/email/packaging/gen_scorecard.py`.

This is a **phased checklist with a hard gate at the real-eval step** — the scorecard MUST come from an actual eval run, never hand-authored numbers.

## Phase 1 — Locate the agent's surfaces

1. **Version source of truth** = the `version:` field in `<agent>/gaia-agent.yaml`. Never invent a parallel scheme.
2. **Canonical README** (where the scorecard is linked + surfaced): for an npm-published agent it is the npm client README (e.g. `hub/agents/npm/<id>/README.md`), NOT a `packaging/README.md`. For a Python-only agent it is `hub/agents/python/<id>/README.md`. Confirm which by checking what `release_agent_<id>.yml` publishes (`README:` env) — the published README is the one to link.
3. **doc-root** = the directory holding that canonical README. The scorecard lives at `<doc-root>/SCORECARD.md` — a **single file updated in place**, versioned via the publish snapshot (same as README.md). **There is no `scorecards/` directory.**
4. **Eval vehicle**: what existing harness produces this agent's accuracy metric? (email → `gaia eval benchmark` over `tests/fixtures/email/`.) If none exists, STOP and surface that — propose the minimal harness before building; do not invent numbers.

## Phase 2 — Write the adapter (harness → payload)

Copy `hub/agents/python/email/packaging/gen_scorecard.py` as the template. The adapter:
- imports ONLY `gaia.eval.release_scorecard` (never the harness or agent package — preserve loose coupling);
- reads the harness output, builds a `ResultPayload`;
- populates `reproduction_command` with the **exact shell commands** to reproduce this scorecard, including all required env vars (`PYTHON_KEYRING_BACKEND`, `GAIA_AGENT_TOOL_TIMEOUT`, `PYTHONPATH`);
- defines **"judged"** explicitly and **raises loudly** if zero results are judged (no silent 0.0);
- records **dataset size** (total labeled examples) and **test_cases_run** (subset executed) as DISTINCT fields;
- stores **repo-relative** paths only (never a local absolute path — it ships in a published artifact);
- records the eval `limit`/config so future regression checks are comparable;
- writes to `<doc-root>/SCORECARD.md` (the single file; `--output-dir` overrides to a directory, but the filename is always `SCORECARD.md`).

Add an offline unit test against a committed sample harness-output fixture (see `tests/fixtures/eval/email_benchmark_scorecard.json` + `tests/unit/eval/test_release_scorecard.py::TestEmailAdapter`) so the adapter is testable without a live model.

## Phase 3 — Run the REAL eval (hard gate — no hand-authored numbers)

The accuracy number must come from an actual run. For the email agent:

```bash
# Real eval needs Lemonade + the model. Prefer AMD hardware (Strix Halo / Ryzen AI);
# the [self-hosted, lemonade-eval] runner is the canonical environment.
GAIA_AGENT_TOOL_TIMEOUT=900 \
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
PYTHONPATH="$(pwd)" \
  <venv>/bin/gaia eval benchmark \
    --model Gemma-4-E4B-it-GGUF \
    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 25 --output-dir <persistent-dir>

PYTHONPATH="$(pwd)" \
<venv>/bin/python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir <persistent-dir> --limit 25
# → writes hub/agents/npm/agent-email/SCORECARD.md in place
```

**Headless gotchas (see memory `project-email-benchmark-headless-gotchas`):**
- `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` — the email agent's calendar-connector resolution blocks forever on the macOS Keychain (and can stall on Linux SecretService) in non-interactive contexts. Without this it hangs at 0% CPU during agent construction.
- `PYTHONPATH="$(pwd)"` — the benchmark imports `tests.fixtures.email.*`; the console script doesn't add the repo root.
- `GAIA_AGENT_TOOL_TIMEOUT=900` — triage of N emails is one tool call; the 180s default abandons it on slow backends, yielding a degenerate 0-email FAIL run.
- Write `--output-dir` to a **persistent** dir, not `/tmp` (cleared on session resume).
- Record honestly: if the metric is low for a known reason (e.g. a taxonomy/label mismatch), put the explanation in the adapter's `methodology` string and link the tracking issue — never inflate the number.

## Phase 4 — Surface, link, and gate

1. **Link + surface** from the canonical README: a one-line `Eval scorecard (vX.Y.Z): aggregate N/100 … ([./SCORECARD.md](./SCORECARD.md))`. The relative link must resolve in-repo.
2. **npm `files`**: if the agent publishes on npm, add `SCORECARD.md` to `package.json` `files`. **Do not** add a `scorecards/` directory — only the single current file ships.
3. **Hub display**: a published scorecard surfaces on the agent's hub page / Agent UI detail view (see `workers/agent-hub` + `AgentDetailModal.tsx`); ensure the publish step passes `--eval-scorecard <doc-root>/SCORECARD.md` to `publish_to_r2.py`.
4. **Release gate**: add a `scorecard-gate` job to `release_agent_<id>.yml` and list it in `publish.needs`. The job runs on a GitHub-hosted runner (it only parses committed files — no eval):
   ```bash
   # Presence-only (no previous tag yet):
   python -m gaia.eval.scorecard_gate \
     --scorecard <doc-root>/SCORECARD.md

   # With best-effort previous-release baseline (recommended for CI):
   PREV="$(git describe --tags --abbrev=0 --match 'agent-pkg-<id>-*' "${GITHUB_REF_NAME}^" 2>/dev/null || true)"
   if [ -n "$PREV" ]; then
     python -m gaia.eval.scorecard_gate \
       --scorecard <doc-root>/SCORECARD.md --baseline-ref "$PREV"
   else
     python -m gaia.eval.scorecard_gate \
       --scorecard <doc-root>/SCORECARD.md
   fi
   ```
   The job must NOT have `continue-on-error`, an `environment:`, or a `permissions:` override (inherits `contents: read`; needs no secrets). Fetch full history (`fetch-depth: 0`) so `git describe` resolves.
5. **Auto-update/reject loop**: for re-running on agent changes and refreshing the committed scorecard, see `eval-scorecard.mdx` "Keeping the scorecard current" and the self-hosted refresh workflow — reject-on-worse is the gate; better-or-equal refreshes the committed `SCORECARD.md`.

## Phase 5 — Verify (evidence before "done")

Run and capture: the generated `SCORECARD.md`; the gate **passing** on it (exit 0); the gate **blocking** a manufactured regression (exit 1, via `--baseline-file` with a higher-scoring card) and a missing card (exit 1); a by-hand recompute of the aggregate from `aggregate.components` matching the recorded value. Run `python util/lint.py --all` and the eval unit tests. These are the PR's real-world proof.

## Versioning

- **Patch** release → `carry_forward(prev_scorecard_path, new_version)` reads the version from the front matter of the current `SCORECARD.md` (not from the filename) and copies results verbatim, sets `inherited_from`; do NOT re-run the eval.
- **Minor/major** release → re-run the eval (Phase 3); `carry_forward` refuses a non-patch bump with a "re-run" error.
