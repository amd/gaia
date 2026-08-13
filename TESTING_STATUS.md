# GAIA agent + TUI — testing status

Live log of validating the flagship agent through the Go TUI on branch
`feat/gaia-flagship-agent-2804` (PR #2932). Updated continuously.

**Last updated:** 2026-08-13 16:30
**Backend:** Lemonade / Gemma-4-E4B-it-GGUF (GPU). `--use-claude` merged and built; blocked on `ANTHROPIC_API_KEY`
**Harness:** `C:\Users\14255\Work\gaia-tui-test\` (launch-tui.ps1 + driver.py), single TUI on control port 8817

---

## Where it stands

**GitHub triage works end to end.** The agent loads the skill and returns the
real backlog, matching `gh` exactly. Getting there took fixing two agent bugs
that made every shell command fail — the second one made `gh` unusable outright.

| Rung | Result | Time |
|---|---|---|
| L1 arithmetic (`391`) | pass | 6.0s warm |
| L2 store memory | pass | ~20s |
| L3 recall across turns (`Teal`) | pass | 14.8s |
| L4 shell tool | pass | 52s |
| L5 load `github-triage` | pass, occasionally flaky | 39–72s |
| L6 skill persists across turns | pass | — |
| **L7 real `gh` triage** | **pass — exact match** | **24.6s** |

L7 verified against ground truth, `⚠️` and all:

```
• #2962: ⚠️ Release notes generation failed for v0.23.0
• #2961: Release notes generate as long marketing prose instead of short bullets
• #2960: ci(eval): agent-eval gate fails preflight on every PR — judge key arrives
         empty, so no scorecard is trustworthy
```

Before the fixes this same query ran 400s+ and answered *"a significant
networking bottleneck between this agent and GitHub services"* — a confident
fabrication. It had never successfully read anything.

---

## Fixed and committed

**1. Control-plane logging painted over the live screen** — `e71234c0`
`--dev` turns control debugging on, and every control-API request wrote a
`[control] …` line straight to stderr while Bubble Tea held the alt screen. The
screen you turn `--dev` on to read was the screen the logging destroyed.
Diagnostics now append to `~/.gaia/logs/gaia-tui.log` (or `GAIA_TUI_HOME`).
4 new tests. This was the "strange printout" reported from the UI.

**2. The agent log could not be attributed** — `432dd3c7`
All agents appended to one `gaia-agent.log` with no pid. With parallel tasks
running their own agents, the file interleaved and a neighbour's tool timeout
read as this session's failure. Cost about an hour and nearly produced a false
shell-tool bug report. Added `GAIA_AGENT_LOG` override + pid on every line.
3 new tests.

**3. The shell tool silently discarded non-ASCII output** — `e72c2a01`
`gh issue list` returned rc=0 with **empty stdout**, so the model reported on a
backlog it had never read. Bare `text=True` decodes with the OS locale codec
(cp1252) inside subprocess's reader thread; one unmappable byte kills that thread
and `run()` still reports success. Trigger was ordinary — issue #2962's title
contains `⚠️`. Now decodes utf-8 with `errors="replace"`. 3 new tests.

**4. Shell commands inherited the agent's stdin and hung forever** — `530d5141`
*(the one that actually blocked triage)*
Every `gh` call hung the full 180s, left an orphaned `gh.exe`, and the agent
blamed the network — while the same command took 0.07s from a shell.
`capture_output` redirects stdout/stderr but leaves **stdin inherited**: the TUI's
pipe, open and never written, with no human behind it. Anything reading or probing
it waits forever. `subprocess.run`'s timeout doesn't save it — on expiry it kills
the `cmd.exe`, then calls `communicate()` again with **no timeout**, blocking on
pipes the surviving grandchild holds. That is the 30s→180s escalation and the
orphan-per-attempt. Now `stdin=subprocess.DEVNULL`. 2 new tests, 0 orphans after.

**5. Harness leaked a cmd window per launch**
`cmd /k` kept every shell alive after the TUI exited — 12 had piled up. Launcher
now kills any predecessor (process + host shell + discovery file) before
starting, enforcing one TUI at a time.

**6. Test playbook updated** — `d64b8b42`
Six traps folded back into `.claude/skills/testing-the-gaia-agent/` so the harness
can run unattended: private agent log, installing `gaia-agent` with `--no-deps`,
the missing starter pack, `gh` needing the skill loaded, restarting Lemonade
correctly, and an ordered procedure for diagnosing the 180s shell hang.

---

## Open issues

**A. The flagship agent ships with zero skills.** *(highest impact)*
`hub/skills/` is described in its own test suite as *"the shipped starter skill
pack… the product's first impression"* — 9 skills including `github-triage`. But
`gaia_agent/skills/` contains only `.gitkeep`, **nothing stages `hub/skills/`
into the package**, and every `skills:` / `skill_sets:` / `default_skill_set:`
key in `gaia-agent.yaml` is commented out. So a user who installs the wheel gets
an agent that can load nothing. Testing only worked after I hand-copied the skill
into `~/.gaia/skills/`.

**B. The agent substitutes a different skill and reports success.**
Asked for `github-triage` (not yet installed), it called `load_skill` correctly,
got a correct `SkillNotFoundError`, then loaded `github-issue-response` instead
and replied *"Got it! The github-issue-response skill is now active."* The code
is right; the model silently swapped the user's request and never said the
requested skill did not exist. Response-quality defect — candidate for the
personality skill.

**C. `pwd` is labelled DESTRUCTIVE.**
The confirmation modal says *"This is a destructive action and may not be
reversible"* for a read-only `pwd`. False alarms train users to approve blindly.

**D. Skill loading is flaky.**
The same `Load the github-triage skill` failed with "isn't among your currently
installed skills" and then succeeded ~2 minutes later, unchanged on disk, with
the CLI discovering it correctly throughout.

**E. Cold start is ~3.5 minutes with only a spinner.**
First turn took 238s (ttft 228s) loading Gemma-4-E4B + nomic-embed. Warm turns
are 6s. The UI shows only "still working — local model, usually 60-90s", which
undersells it by 3×.

**F. Lemonade is a single-slot single point of failure.**
It died outright mid-test (connection refused, no process). Parallel agents
contend for one slot. Restart is `LemonadeServer.exe` — note `lemonade.exe serve`
is wrong (that binary is the client and rejects `serve`).

---

## Verified working

- **`gh` permission gate** — all 8 policy cases match expected exactly.
  `issue list`, `auth status`, `api repos/...` allowed; `auth token`,
  `issue create`, `api -X POST`, `alias set`, `extension install` refused.
- **`--bypass-permissions`** already exists on this branch, with `/bypass on|off`
  working mid-session. Toggling verified in both directions.
- **Skill grant exempts `gh` from prompting** — with bypass OFF, loading
  `github-triage` and running `gh` produced no confirmation modal, exactly as the
  skill documents.
- **Memory across turns** — stored and recalled correctly.

---

## Corrections to my own earlier reads

Recording these because a wrong bug report costs more than a missing one.

- Guessed external executables hang when the agent's stdin is a pipe. **Wrong** —
  a direct repro ran `gh --version` in 0.08s in a daemon thread with piped stdin.
- Attributed 180s `run_shell_command` timeouts to a shell-tool bug. **Wrong** —
  the tool returns in 0.07s standalone; the timeouts track Lemonade contention and
  its eventual death.
- Read `gofmt -l` output as real formatting drift. **Wrong** — CRLF artifact of a
  Windows checkout, present on files never touched.

---

## Next

1. Fix A — stage `hub/skills/` into the agent package so the product ships usable.
2. Author the auto-loaded personality skill (targets B and response quality).
3. `--use-claude` — merged and built; needs `ANTHROPIC_API_KEY` to run.
4. Fix the 5 sibling `text=True` call sites in `agents/base/`.
5. Robustness sweep: empty input, agent crash, Esc cancel early/mid-generation.

## Delivery note

Cannot push to `amd/gaia` — the authenticated account (`kovtcharov`) gets 403;
the PR branch lives on `amd/gaia`, not a fork. Work is committed locally and
mirrored to `origin` (`kovtcharov/gaia`). Landing it needs a PR from the fork
into `feat/gaia-flagship-agent-2804`.
