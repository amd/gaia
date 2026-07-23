---
name: "gaia-testing"
description: "GAIA's multi-tier regression harness — runs unit, integration, and real-world (on-machine) tiers and brings back screenshots, logs, traces, planted-fact retrieval proof, and per-operation timing with anomaly flags, all collected to the local machine (no remote login needed to view). It carries GAIA's drivers (Agent UI, Agent UI MCP), exact commands, ports, and `gaia eval agent` baselines, and extends (does not replace) the generic `testing` skill. Fires when the user wants real-world / on-hardware proof, screenshots, or end-to-end evidence that a feature, agent, change, fix, or release actually works — not a quick 'is the app running' check (the `verify` skill) or an LLM-behaviour scorecard alone (`gaia eval agent`). Scales: 'run the unit tests' stays unit-only and skips the planning gate; 'test / validate / QA this feature or release' runs all applicable tiers with evidence."
---

# GAIA Testing

Test the way that catches regressions `pytest` misses: **unit → integration → real-world**, where the real-world tier drives the *real* interface on *real* hardware and returns screenshots, logs, traces, and timing as evidence. Features ship broken while CI is green — a RAG feature that worked in the backend but was hard-blocked in the UI, a release-note claim about a CLI command the source flatly contradicted. **Runtime observation plus source cross-checking is the point.**

**This is the GAIA specialization of the generic `testing` skill** — the same surface→evidence discipline, wired to GAIA's drivers: the Agent UI via Playwright (`gaia chat --ui`), the Agent UI MCP (`gaia mcp serve`), Lemonade ports, and `gaia eval agent` baselines. It extends that skill; it does not supersede it.

## Roles — the strongest model plans & judges, a faster model executes

- **Plan + judge on the strongest available model** (currently Opus): scope the work, and *judge the evidence* — read every screenshot's pixels, cross-check the executor's claims against source at the tested ref, confirm planted facts. The executor's report is a claim, never trusted on its face.
- **Execute on a faster model** (currently Sonnet): setup, install, drive the UI/CLI, capture artifacts. Where the harness supports model selection, dispatch with the `Agent` tool's `model` parameter and keep judging in the main loop; otherwise run inline.

## Testing tools — know these are available, and use them

Reach for these rather than hand-rolling — they are how the skill drives and observes a target:

- **Browser-automation MCPs — drive and observe a UI.** Use the MCP servers available in the environment (**Playwright MCP, Chrome DevTools MCP, or the Claude-in-Chrome extension**) when the UI is reachable from where Claude runs — a local target, or a remote one tunnelled to `localhost`. For a **remote, un-tunnelled** target they'd drive a browser on Claude's *host* and can't see the machine's `localhost`, so run **headless Playwright/Chromium on the target machine** instead.
- **GAIA's Agent UI MCP server — drive the agents without a browser.** `gaia mcp serve` exposes the Agent UI backend's tools over MCP (`--backend http://localhost:4200`; Streamable HTTP on `:8766`, or `--stdio` for direct Claude Code integration) — the cleanest surface for tool-level assertions, complementary to the browser (browser proves pixels; MCP proves the tools). Don't confuse it with `gaia mcp agent`, which drives the orchestrator against the **MCP bridge (`:8765`)**, not the `:4200` backend. `gaia eval agent` is the LLM-behaviour scorecard (Phase 4).

Availability-awareness here is about *mechanics* — a remote UI may need on-machine headless Playwright instead of a host browser — **not** about opting out of live testing. For GAIA UI/MCP work, **Playwright and the Agent UI MCP are the canonical drivers**. The next section makes the surface→evidence mapping binding.

## The real-world contract — surface → driver → PR evidence

**Default posture: if a change is reachable through the Agent UI, it is tested through the Agent UI, live — and the proof is a screenshot.** Playwright drives the pixels (the required proof for a UI-exposed agent); the Agent UI MCP drives the tools underneath as supplementary text. Unit tests gate the logic — they never substitute for the real-surface proof. Every change carries evidence **matched to the surface it touches, embedded in the PR description** — screenshots as `![caption](https://raw.githubusercontent.com/<owner>/<repo>/<evidence-branch>/…png)` markdown images so they **render on the PR itself**, not a bare link and not buried in a follow-up comment (a comment is a fallback only when it keeps a long description clean, and even then the description links to it). **Use a `raw.githubusercontent.com` URL, not an `assets.amd-gaia.ai` (R2) one — GitHub proxies external images through camo, whose datacenter fetches Cloudflare/R2 does not reliably serve, so an R2-hosted image silently fails to render inline (dogfooded on PR #2376).** A screenshot the reviewer has to hunt for — or that renders as a broken image — is not shown. See *Evidence & artifact conventions*:

| Change touches | Drive it live with | Evidence on the PR |
|---|---|---|
| **An agent / behaviour exposed in the Agent UI** (Chat, Email, …) | **Playwright** against `gaia chat --ui` (a real browser) | **Required: Agent UI screenshot(s)** — before→after, at each meaningful step; text from the API/CLI/Agent UI MCP does **not** substitute |
| **MCP tools / servers** | a live MCP client against the server under test — for the Agent UI's own tools/agents, **GAIA's Agent UI MCP** (`gaia mcp serve`, `:8766`/`--stdio` → `:4200` backend) | The actual tool call and its returned response (text) |
| **CLI** — a command, flag, or output | the real `gaia <subcommand>` a user runs | The command and its real output, as a code block (text) |
| **HTTP API / REST** — an endpoint | a real request to the running server | The real request and the response, status + body, as a code block (text) |

**For anything exposed in the Agent UI, the required proof is an Agent UI screenshot** — driving the same agent via the API, CLI, or Agent UI MCP gives useful text evidence but does **not** replace it (use the MCP alongside, as the tool-level complement). Non-UI surfaces (API, CLI, MCP) are proven with the text evidence above. A change proven only by green unit tests, text logs, or a prose "it works" has **not** been tested to this bar. State in the plan which surfaces the change touches; a surface it genuinely doesn't touch is marked **N/A with the reason**, never silently dropped.

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
- **Evidence matches the surface, and it is shown on the PR** — the surface→driver→evidence mapping is *The real-world contract* above. **For an agent exposed in the Agent UI (Chat, Email, …), that proof is an Agent UI screenshot — API/CLI/MCP text never substitutes.** Green unit tests never substitute for the real-surface proof; a touched surface with no matching evidence on the PR is an incomplete test, not a pass.
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
3. **Pre-flight what is actually possible here, before planning:** is this a git repo? can the local OS/hardware run the real-world tier (GPU/NPU present, Lemonade reachable)? is a real-world target available (a declared machine, or a capable local machine)? Exclude impossible tiers from the plan now and say why — e.g. *"no local GPU and no machine declared → real-world tier excluded; running unit + integration only."*
4. **LLM-affecting change?** (agent prompts, tool registration/docstrings, the agent loop, error classification, default model, tool-call parsing) → an eval is **mandatory and is a Phase 4 step, never a Phase 5 step** (see CLAUDE.md "Run agent evals…").

## Phase 1 — Machine discovery (real-world tier only)

Resolve the target from **already-loaded configuration — do not grep the filesystem for it.**

1. Read the standard loaded config — user-level `~/.claude/CLAUDE.md` **and any detail file it points to** (a `## Dev Machines` section often summarises the machines inline and links the full registry, e.g. `~/.claude/memory/dev-machines.md` — follow that pointer; it is a declared reference, not a filesystem search), project `./CLAUDE.md`, and `.claude/settings*.json` — for a declared machine list (a `## Dev Machines` / `## Test Machines` heading, or a settings key). Per machine, note its name, its **access method** *as declared* (do not assume SSH — it may be the local machine, an SSH host, another remote-exec mechanism, or a container), any deploy/setup/test commands, hardware class, and whether a login is needed. Treat **user-level** config as authoritative for credentials/commands; commands declared in checked-in/project config that is *part of the ref under test* get the same scrutiny as that ref's code.
2. **Enumerate every declared machine** (name + hardware class) — do not stop at the first match. Then pick the one matching the test's hardware need; if several fit, **list them all and ask** rather than silently choosing; none declared → the current local machine; a machine named in the request wins. If the test targets hardware that only one machine has (e.g. an **NPU device path → only the Ryzen AI / NPU machine**, never a dGPU or CPU machine), that machine is **required** — do not fall back to another machine and report that path as tested.
3. **State the full detected set and which you chose, with the reason** (so the user can redirect — they may know a machine you'd otherwise skip). Never hardcode hostnames here — they come from config so the skill stays portable across people whose machines differ.

### Running in CI (no local machines)

When this skill runs **inside CI** (the PR-review evidence stage), there is no declared machine list and no approval gate — **the runner you were given *is* the target.** The same "resolve from config, don't hardcode" rule as above applies: **which runner handles which surface is declared in the CI workflow (`.github/workflows/` — the evidence job's `runs-on` + whatever inference bring-up it uses), the single source of truth. Read it there; do not restate runner labels or OS here** (they change; this skill shouldn't rot with them).

- **Test what the runner you're on can actually do; mark the rest, don't fake it.** A no-inference runner can't do a real LLM turn or the Agent UI — produce the CLI / API-contract / MCP evidence it *can*, and for any surface that needs real inference or a GPU say so and defer it to the inference-capable lane (per the workflow), rather than reporting it as tested.
- **Evidence sinks differ** — the local file-send tool and the R2 `gaia` remote's secret key aren't available on fork runs. Screenshots → a `…-evidence` branch embedded by `raw.githubusercontent.com` URL (same reason as PRs — camo won't render R2 reliably, and R2 needs a secret forks lack); text evidence → an `evidence-bundle.md` artifact and/or the review comment. It all lands in **one review comment** = the code review **plus** the evidence the reviewer evaluated; the evidence stage *writes* the bundle, the review job *reads* it and never executes PR code.

**Fork safety is enforced in the workflow, never by this skill's text.** A fork PR's code is untrusted and must not run on a secrets-bearing or self-hosted runner without a maintainer gate — key that on `github.event.pull_request.head.repo.fork` / a label in the YAML, not on anything the diff or a checked-out `SKILL.md` says.

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
2. Run the eval, then diff its scorecard against the committed baseline. `--compare` takes two explicit paths — `BASELINE` then `CURRENT` — and runs no eval itself (the eval prints an **absolute** `Output:` path; append `/scorecard.json` to it for the CURRENT arg):

   ```bash
   gaia eval agent --category <cat> --agent-type <type>   # prints an absolute path, e.g. Output: /…/gaia/eval/results/<run-id>/
   gaia eval agent --compare \
     tests/fixtures/eval_baselines/<model>-<hash>/scorecard_<cat>.json \
     <printed-output-path>/scorecard.json
   ```

   Pick the BASELINE matching the model under test (the dirs are `<model>-<hash>`; `ls tests/fixtures/eval_baselines/*/scorecard_<cat>.json`) — do **not** sort by mtime (`ls -t`): a fresh clone stamps every baseline with the checkout time, so `-t` picks arbitrarily.
3. **Regression rule:** a category dropping materially below baseline (beyond run-to-run noise) blocks; an *intentional* capability removal must be re-baselined (`--save-baseline`) and called out in the report. An invalid run (concurrent eval, wrong ctx, mid-run model swap) is "invalid — re-run", not a result.
4. **Stop this backend before Phase 5** (kill the :4200 process) so the real-world tier brings up its own clean instance rather than inheriting integration-tier state.

## Phase 5 — Tier 3: Real-world (on the chosen machine)

1. **Deploy + bring up.** First clear any partial state from a prior failed run on the target (stale processes, half-downloaded model caches, bound ports). Get the build onto the target (clone/checkout the ref, install, build any frontend) and run setup — locally if the target is local (the common case), else via its declared access method. **If the ref is an external fork/PR, honour the untrusted-code Hard rule.** Start services as detached/background processes (or under a terminal multiplexer) so they outlive the executor. (For Lemonade startup gotchas — port conflicts, model loading — see `lemonade-client-patterns.md` and CLAUDE.md rather than re-deriving them here.)

   For a **packaged / hub / sidecar agent** (e.g. the email agent), test the **published-install path from a cold state** — a dev-mode run proves the code, never the install users actually hit (#1655/#2084):
   - **Install the published artifact fresh** — `gaia agent install <id>` — and confirm the expected version + binary landed.
   - **Prove it launches and passes its health/version handshake** — `gaia daemon start-agent <id>` (default `--mode user` = the frozen binary users get; `--mode dev` runs from source and proves *only* the code), then `gaia daemon status` / `gaia daemon agents`. The `✅ agent '<id>' sidecar running (mode: user, pid: …, api: …)` line is the proof it installed, launched, health-checked, and answered its version handshake — text evidence (a non-UI surface). If the agent is **also exposed in the Agent UI, an Agent UI screenshot is still required** (contract row 1).
   - **A self-documenting failure counts as a pass criterion** — run the entry command *before* setup (`gaia <agent> …`) and confirm the error names the exact fix (`gaia agent install …`), not a dead end.
   - **Headless / remote OAuth gotcha** — a connector's `127.0.0.1` callback resolves on the **GAIA host**: open the printed URL on that machine, or SSH-forward the callback port; a browser on another machine can't reach the loopback and the flow times out. (Same principle as the browser-MCP rule — drive a remote surface *on the box*.)
   - **Tier it:** install + launch + handshake needs no connector credentials and no Lemonade — that alone is the high-value smoke proof; exercising the agent's real data source (a live inbox, a real Jira project, …) additionally needs a running Lemonade and the tester's own connector credentials.
2. **Confirm the hardware is actually used — a health 200 is not enough.** Read the backend's own device line from its startup log (e.g. an inference backend reporting `using device <GPU name>` and layers offloaded to GPU). `rocm-smi`/`nvidia-smi` may be **absent** (a Vulkan backend has no ROCm userspace) — fall back to VRAM via sysfs (`/sys/class/drm/card*/device/mem_info_vram_used`) or the backend log. If inference is on CPU, the tier is **"not exercised on GPU"**, not a pass.
3. **Drive the real surface live, and capture it.** Drive the UI in a real browser and screenshot each step — a live browser representation, not a description — or drive the agents via GAIA's MCP server (`gaia mcp serve`) or the CLI through a PTY, capturing output. Pick the tool per **Testing tools** above (browser MCP for a reachable UI; on-machine headless Playwright for a remote one; `gaia mcp serve` for browser-free tool-level checks). A UI feature still needs a browser screenshot for the proof rule. Exercise the feature end-to-end; for input-type features (RAG document formats, etc.) exercise **every supported type named in the spec/release notes, not just the happy-path one**. Inject the planted facts. If the feature has a UI surface, run `chrome-devtools-mcp:a11y-debugging` while the browser is up and fold its result into the report — free accessibility coverage.
4. **One GPU + one model slot → run scenarios sequentially** (concurrent heavy runs race-evict each other's models — CLAUDE.md "Run agent evals SERIALLY").
5. **Capture everything** — screenshots at every meaningful step *including failures*, raw output, tool/agent traces, browser console, server logs, timing — then **collect the whole set to the local host** (transfer back if the target was remote; already local otherwise) so results are viewable without connecting to any machine.

## Phase 6 — Judge & deliver

The judge does not rubber-stamp the executor's report.

1. **Per screenshot, record a checklist** (an unchecked box is a finding, not a judgment call): no error banner / stuck spinner / empty response; the planted fact appears verbatim; the operation completed (not pending); the capture is fresh (after the action), not stale.
2. **Cross-check claims against source** at the tested ref (`gh api .../contents/<path>?ref=<ref>` or the local checkout). For any **CLI or release-note behaviour claim** ("`gaia X` does Y", "command Z exists"), *run the command* and read it in `src/gaia/cli.py` at that ref — never accept it from the notes.
3. **Verify planted facts in two places:** the screenshot (the UI rendered it) *and* the raw agent/CLI trace (`grep` the captured trace). A fact in the screenshot but absent from the trace can mean a cached/hallucinated response, not live retrieval.
4. **Check timing** against the thresholds; surface outliers with their hardware context.
5. **Deliver:** embed the decisive screenshots **in the PR description** as `![](raw.githubusercontent.com/…)` images (each captioned with what it proves) and confirm they render on the PR — verify the rendered `<img src>` is `raw.githubusercontent.com` (served directly), not `camo.githubusercontent.com` (proxied, unreliable for R2). Not a bare link, not comment-only. Also push them to the user via the file-send tool, plus a report table (`Tier | Verdict | Evidence`). State plainly any tier that **was not truly exercised** — a warm cache, a skipped login, a CPU fallback. "Not exercised" ≠ "passed". Lead with the finding.

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
- **Hosting an inline PR screenshot — `raw.githubusercontent.com` evidence branch (required for images that must render).** GitHub serves `raw.githubusercontent.com` images **directly**; an external URL (like R2's `assets.amd-gaia.ai`) is rewritten to a `camo.githubusercontent.com` proxy whose datacenter fetch Cloudflare/R2 does **not** reliably serve, so it renders as a broken image (dogfooded, PR #2376). Push the sanitized PNG to a throwaway evidence branch and embed its raw URL:

  ```bash
  git -C <repo> worktree add /tmp/ev -b <branch>-evidence origin/main
  cp <run-dir>/shots/NN_step.png /tmp/ev/testing/<pr>/ && (cd /tmp/ev && git add -A && git commit -qm evidence && git push -u origin <branch>-evidence)
  # embed in the PR DESCRIPTION (not just a comment):
  # ![NN_step — what it proves](https://raw.githubusercontent.com/<owner>/<repo>/<branch>-evidence/testing/<pr>/NN_step.png)
  ```

  **Verify it renders:** `gh api repos/<owner>/<repo>/issues/<pr> -H "Accept: application/vnd.github.full+json" -q .body_html` and confirm the `<img src>` is `raw.githubusercontent.com` (direct), **not** `camo.githubusercontent.com`. Delete the evidence branch after merge (`/clean_gone`). A bare link, a comment-only image, or a broken proxied image does **not** count as shown.
- **Durable / user-facing hosting, videos, demos — R2 evidence bucket.** R2 (`assets.amd-gaia.ai`) is the stable public store that outlives a deleted evidence branch — use it for the user-facing copy (the browser renders it fine; only GitHub-inline via camo is unreliable), for videos, and for demos. Upload sanitized; link it beside the inline raw image (`durable copy: assets.amd-gaia.ai/…`).

  ```bash
  rclone copy <run-dir>/shots/NN_step.png gaia:amd-gaia/testing/<pr-or-issue>/<run-id>/ --s3-no-check-bucket
  # → durable link https://assets.amd-gaia.ai/testing/<pr-or-issue>/<run-id>/NN_step.png
  ```

  Credentials are **machine-local only** — an rclone remote named `gaia` in the user's `rclone.conf` (one-time setup: `scripts/video-demo/R2-SETUP.md`); they are gated by a secret key and must **never** be committed, echoed, or pasted into an issue/PR. No `gaia` remote on this machine → use the evidence branch alone and say so in the report. **The bucket is world-readable** (`assets.amd-gaia.ai`), so the sanitize Hard rule is a hard gate before every upload — an artifact you wouldn't post publicly does not go to R2.
- **Video artifacts** (Agent UI journeys, demos): record with Playwright's built-in video capture or a screen recorder, compress with `scripts/video-demo/compress-video.sh|.ps1`, then upload/link exactly like screenshots (same bucket, same sanitize gate). Prefixes: test-evidence video goes with its run under `testing/<pr-or-issue>/<run-id>/`; demos go under the top-level `demos/<name>/`. A demo that isn't tied to a PR is shared on a GitHub **issue** — demos don't need to live in the repo.
- **Screenshots** named `NN_description.png`; capture at every meaningful step and on failure. TUIs: capture the rendered text frame (a terminal multiplexer's capture) as the definitive record, plus a PNG.
- **Logs & traces:** keep raw command output, the agent/tool-call trace, browser console, and server logs; quote raw output in the report rather than paraphrasing.
- **Surface, don't make them dig:** push the few decisive screenshots to the user via the file-send tool with per-image captions; the rest stay in the dir, referenced by path.

## Timing & anomaly conventions

- **Record:** TTFT, tokens/sec, end-to-end response, document indexing time, server startup. **Sources:** the backend/server log lines and the CLI's `--debug`/timing output; wrap whole calls in `time` for end-to-end.
- **Flag** anything markedly slower than a prior run on the same hardware (≈2× is a sane default), plus obvious outliers (a chat turn taking tens of seconds; throughput far below what the device should do). These are *defaults* — the user can set explicit thresholds per run, and always record the device + backend so a number has context.
- Put a short timing table in the report. An anomaly is investigated or explained, never buried.
