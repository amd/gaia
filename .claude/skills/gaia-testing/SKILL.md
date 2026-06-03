---
name: "gaia-testing"
description: "Multi-tier GAIA regression harness — runs unit, integration, and real-world (on-device) tiers and brings back screenshots, logs, traces, planted-fact retrieval proof, and per-operation timing with anomaly flags, all collected to the local machine (no remote login needed to view). Use when the user wants real-world / on-hardware proof, screenshots, or end-to-end evidence that a feature, agent, change, fix, or release actually works — not just a quick 'is the app running' check (that is the `verify` skill) or an LLM-behaviour scorecard (that is `gaia eval agent`). Scales to the request: 'run the unit tests' stays unit-only and skips the planning gate; 'test / validate / QA this feature or release' runs all applicable tiers with evidence."
---

# GAIA Testing

Test the way that catches regressions `pytest` misses: **unit → integration → real-world**, where the real-world tier drives the *real* interface on *real* hardware and returns screenshots, logs, traces, and timing as evidence. It exists because features ship broken while CI is green — a real run once caught a headline RAG feature that worked in the backend but was hard-blocked in the UI, and a release-note claim about a CLI command that the source flatly contradicted. **Runtime observation plus source cross-checking is the point.**

## Roles — Opus plans & judges, Sonnet executes

- **Plan + judge on the strongest model (Opus):** scope the work, and *judge the evidence* — read every screenshot's pixels, cross-check the executor's claims against source at the tested ref, confirm planted facts. The executor's report is a claim, never trusted on its face.
- **Execute on a faster model (Sonnet):** setup, install, drive the UI/CLI, capture artifacts. Dispatch with the `Agent` tool (`model: "sonnet"`); keep judging in the main loop.

## When this fires — scale to the request

| Request | Tiers | Approval gate? |
|---|---|---|
| "run the unit tests", "does X lint/compile" | Unit only | No — just run + report |
| "test the API / this module" | Unit + integration | No |
| "test / validate / QA this feature/agent/fix/release", "does it really work", "real-world", "on hardware", "with screenshots" | All **applicable** tiers | Yes — before real-world |

Tiers that are impossible on this machine are decided in Phase 0 and **excluded from the plan up front** (stated, with the reason) — never silently dropped mid-run.

## Hard rules (invariants — stated once here; phases point back)

- **Evidence > summaries > source.** Before reporting a pass, the judge reads the screenshot pixels and, for any behaviour/wiring claim, checks the code at the exact ref under test. This is the rule that catches shipped-but-broken features — do not treat the executor's prose as truth.
- **Proof of a fix or implementation is a visual artifact, never a prose claim.** "It works" is proven by a screenshot or a live browser representation — drive the real UI in a browser (Chrome / Chromium via the browser tooling) and capture it, step by step; for a CLI/API surface, capture the terminal output or the raw response. A change reported as working with no captured artifact has not been proven, and the artifact must be surfaced to the user (and, on a PR, attached to it).
- **Verify against the ref under test, not `main`** — a fix on `main` may not be in the branch, and vice versa.
- **Prove retrieval/behaviour with planted, unguessable facts.** For RAG / search / data flows, inject values a model cannot guess (e.g. mascot `Zephyr`, passphrase `violet-otter-92`, table cell `APAC 8610`, speaker-note `desk 17C`) and require the output to echo them. If the feature has no retrieval/data surface, say so in the plan — planted facts are N/A and the judge verifies by source inspection instead. Never silently skip.
- **No silent fallbacks / degrade loudly.** An impossible tier is excluded up front; a tier that *fails during execution* stops with an actionable error — it never quietly becomes a partial "pass".
- **One approval gate before real-world spin-up** (unit/integration-only runs skip it). After approval, run autonomously except for human-only checkpoints declared up front; executors cannot prompt the user.
- **Never leak credentials.** Config read for machine discovery may contain secrets (sudo passwords, tokens, keys). Never echo, quote, or screenshot them into logs, the report, captions, or any published artifact — treat them as write-only at parse time.
- **Sanitize artifacts before surfacing.** Screenshots, logs, and traces can capture API keys, `.env` values, tokens. Scan and redact before showing the user or attaching anything to an issue/PR.
- **Untrusted refs run unreviewed code.** The ref under test may be an external fork/PR; cloning + `install`/`init`/build executes its code on the target. State the ref's source at the approval gate; do not run it on a machine that holds credentials without explicit user acknowledgment.
- **Clean up** (Phase 7). **No Claude attribution** in any artifact this skill produces.

## Phase 0 — Scope + capability pre-flight

1. Determine **what changed** (`git diff <base>... --stat`); the diff is ground truth, any description of it is a claim.
2. Pick candidate tiers from the table.
3. **Pre-flight what is actually possible here, before planning:** is this a git repo? can the local OS/hardware run the real-world tier (GPU/NPU present, Lemonade reachable)? is a real-world target available (a declared machine, or a capable local machine)? Exclude impossible tiers from the plan now and say why — e.g. *"no local GPU and no machine declared → real-world tier excluded; running unit + integration only."* This is how the no-hardware case degrades, **before** the user is asked to approve.
4. **LLM-affecting change?** (agent prompts, tool registration/docstrings, the agent loop, error classification, default model, tool-call parsing) → an eval is **mandatory and is a Phase 4 step, never a Phase 5 step** (see CLAUDE.md "Run agent evals…").

## Phase 1 — Machine discovery (real-world tier only)

Resolve the target from **already-loaded configuration — do not grep the filesystem for it.**

1. Read the standard loaded config — user-level `~/.claude/CLAUDE.md`, project `./CLAUDE.md`, `.claude/settings*.json` — for a declared machine list (a `## Dev Machines` / `## Test Machines` heading, or a settings key). Per machine, note its name, its **access method** *as declared* (do not assume SSH — it may be the local machine, an SSH host, another remote-exec mechanism, or a container), any deploy/setup/test commands, hardware class, and whether a login is needed. Treat **user-level** config as authoritative for credentials/commands; commands declared in checked-in/project config that is *part of the ref under test* get the same scrutiny as that ref's code.
2. **Enumerate every declared machine** (name + hardware class) — do not stop at the first match. Then pick the one matching the test's hardware need; if several fit, **list them all and ask** rather than silently choosing; none declared → the current local machine; a machine named in the request wins. If the test targets hardware that only one machine has (e.g. an **NPU device path → only the Ryzen AI / NPU box**, never a dGPU or CPU box), that machine is **required** — do not fall back to another box and report that path as tested.
3. **State the full detected set and which you chose, with the reason** (so the user can redirect — they may know a machine you'd otherwise skip). Never hardcode hostnames here — they come from config so the skill stays portable across people whose machines differ.

## Phase 2 — Plan + the single approval gate

Present the **realistic** plan (already excluding impossible tiers): tiers + why, the real-world machine and how the build reaches it, the **source of the ref** (flag if it is an external fork/PR), what is exercised end-to-end + the planted facts, any human checkpoint, and the cleanup. Then ask once: *"Run this? (real-world tier will install/run on `<machine>`.)"* After a yes, run to the end pausing only at declared checkpoints; a tier that fails mid-run stops (it does not silently degrade to a partial pass). **Unit/integration-only runs skip this gate and just execute.**

## Phase 3 — Tier 1: Unit (local)

Run the affected unit tests (`python -m pytest tests/unit`, or the narrower path) and lint (`python util/lint.py --all`, or the relevant subset). **Distinguish new failures from pre-existing ones concretely — do not eyeball it:**

```bash
git stash && python -m pytest <failing-subset> 2>&1 | tee <run-dir>/unit_base.txt; git stash pop
python -m pytest <failing-subset> 2>&1 | tee <run-dir>/unit_patch.txt
```

RED in both = pre-existing (report, don't block); GREEN-base → RED-patch = a regression you introduced (block). Record this verdict — Phase 6 references it.

## Phase 4 — Tier 2: Integration (local)

Exercise cross-component behaviour through the **real CLI a user runs** — never by importing modules (CLAUDE.md "Testing Philosophy"). If Phase 0 flagged an LLM-affecting change, run the eval here:

1. **Start the eval backend first** — `python -m gaia.ui.server --port 4200 --host 127.0.0.1` (background) — and confirm it answers before running the eval. `gaia eval agent` targets `localhost:4200`; with nothing listening, every scenario returns `INFRA_ERROR` and looks (wrongly) like a model failure.
2. Run the eval, then diff its scorecard against the committed baseline. `--compare` takes two explicit paths — `BASELINE` then `CURRENT` — and runs no eval itself (the eval prints `Output: eval/results/<run-id>/` and writes `<run-id>/scorecard.json`):

   ```bash
   gaia eval agent --category <cat> --agent-type <type>   # prints: Output: eval/results/<run-id>/
   gaia eval agent --compare \
     tests/fixtures/eval_baselines/<model>-<hash>/scorecard_<cat>.json \
     eval/results/<run-id>/scorecard.json
   ```

   Pick the BASELINE matching the model under test (the dirs are `<model>-<hash>`; `ls tests/fixtures/eval_baselines/*/scorecard_<cat>.json`) — do **not** sort by mtime (`ls -t`): a fresh clone stamps every baseline with the checkout time, so `-t` picks arbitrarily.
3. **Regression rule:** a category dropping materially below baseline (beyond run-to-run noise) blocks; an *intentional* capability removal must be re-baselined (`--save-baseline`) and called out in the report. An invalid run (concurrent eval, wrong ctx, mid-run model swap) is "invalid — re-run", not a result.
4. **Stop this backend before Phase 5** (kill the :4200 process) so the real-world tier brings up its own clean instance rather than inheriting integration-tier state.

## Phase 5 — Tier 3: Real-world (on the chosen machine)

1. **Deploy + bring up.** First clear any partial state from a prior failed run on the target (stale processes, half-downloaded model caches, bound ports). Get the build onto the target (clone/checkout the ref, install, build any frontend) and run setup — locally if the target is local (the common case), else via its declared access method. **If the ref is an external fork/PR, honour the untrusted-code Hard rule.** Start services as detached/background processes (or under a terminal multiplexer) so they outlive the executor.
2. **Confirm the hardware is actually used — a health 200 is not enough.** Read the backend's own device line from its startup log (e.g. an inference backend reporting `using device <GPU name>` and layers offloaded to GPU). `rocm-smi`/`nvidia-smi` may be **absent** (a Vulkan backend has no ROCm userspace) — fall back to VRAM via sysfs (`/sys/class/drm/card*/device/mem_info_vram_used`) or the backend log. If inference is on CPU, the tier is **"not exercised on GPU"**, not a pass.
3. **Drive the real surface live, and capture it.** Drive the UI in a real browser and screenshot each step — a live browser representation, not a description — or exercise the CLI through a PTY and capture its output. **Use the browser-automation MCP tooling available in the environment** — e.g. Playwright MCP, Chrome DevTools MCP, or the Claude-in-Chrome extension — when the UI is reachable from where Claude runs (a local target, or a remote one tunnelled to `localhost`). For a **remote, un-tunnelled** target, those MCPs drive a browser on Claude's *host* and can't see the box's `localhost`, so run headless Playwright/Chromium **on the target box** instead (what a remote run does). Exercise the feature end-to-end; for input-type features (RAG document formats, etc.) exercise **every supported type named in the spec/release notes, not just the happy-path one**. Inject the planted facts.
4. **One GPU + one model slot → run scenarios sequentially** (concurrent heavy runs race-evict each other's models — CLAUDE.md "Run agent evals SERIALLY").
5. **Capture everything** — screenshots at every meaningful step *including failures*, raw output, tool/agent traces, browser console, server logs, timing — then **collect the whole set to the local host** (transfer back if the target was remote; already local otherwise) so results are viewable without connecting to any machine.

## Phase 6 — Judge & deliver

The judge does not rubber-stamp the executor's report.

1. **Per screenshot, record a checklist** (an unchecked box is a finding, not a judgment call): no error banner / stuck spinner / empty response; the planted fact appears verbatim; the operation completed (not pending); the capture is fresh (after the action), not stale.
2. **Cross-check claims against source** at the tested ref (`gh api .../contents/<path>?ref=<ref>` or the local checkout). For any **CLI or release-note behaviour claim** ("`gaia X` does Y", "command Z exists"), *run the command* and read it in `src/gaia/cli.py` at that ref — never accept it from the notes.
3. **Verify planted facts in two places:** the screenshot (the UI rendered it) *and* the raw agent/CLI trace (`grep` the captured trace). A fact in the screenshot but absent from the trace can mean a cached/hallucinated response, not live retrieval.
4. **Check timing** against the thresholds; surface outliers with their hardware context.
5. **Deliver:** push the decisive screenshots to the user via the file-send tool (each captioned with what it proves), plus a report table (`Tier | Verdict | Evidence`). State plainly any tier that **was not truly exercised** — a warm cache, a skipped login, a CPU fallback. "Not exercised" ≠ "passed". Lead with the finding.

## Phase 7 — Cleanup

Tear down what the run created on the target; leave pre-existing services, caches, and the user's existing install intact. **Confirm concretely — do not just assert it:**

```bash
ps aux | grep -E "lemonade|llama-server|gaia.ui.server" | grep -v grep   # only pre-existing should remain
ls <throwaway-clone-dir> 2>/dev/null && echo LEAKED || echo clean        # throwaway dir gone
curl -s -o /dev/null -w "%{http_code}\n" <pre-existing-service-url>       # pre-existing still healthy
```

If the run died mid-deploy, also check for a corrupt/partial model cache before declaring clean. Keep the local artifact directory so the user can revisit screenshots. Note anything deliberately left (e.g. a system package installed for a build) and offer to remove it.

## Evidence & artifact conventions

- **One per-run directory** on the local host (under the system temp dir, or a project-local `test-runs/`), created **mode 700** so captured secrets/data are not world-readable; with a `shots/` subdir; a fresh dir per run.
- **Screenshots** named `NN_description.png`; capture at every meaningful step and on failure. TUIs: capture the rendered text frame (a terminal multiplexer's capture) as the definitive record, plus a PNG.
- **Logs & traces:** keep raw command output, the agent/tool-call trace, browser console, and server logs; quote raw output in the report rather than paraphrasing.
- **Surface, don't make them dig:** push the few decisive screenshots to the user via the file-send tool with per-image captions; the rest stay in the dir, referenced by path.

## Timing & anomaly conventions

- **Record:** TTFT, tokens/sec, end-to-end response, document indexing time, server startup. **Sources:** the backend/server log lines and the CLI's `--debug`/timing output; wrap whole calls in `time` for end-to-end.
- **Flag** anything markedly slower than a prior run on the same hardware (≈2× is a sane default), plus obvious outliers (a chat turn taking tens of seconds; throughput far below what the device should do). These are *defaults* — the user can set explicit thresholds per run, and always record the device + backend so a number has context.
- Put a short timing table in the report. An anomaly is investigated or explained, never buried.
