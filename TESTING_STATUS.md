# GAIA agent + TUI — testing status

Live log of validating the flagship agent through the Go TUI on branch
`feat/gaia-flagship-agent-2804` (PR #2932). Updated continuously.

**Last updated:** 2026-08-13 23:40
**Backend:** Claude (`--use-claude`, claude-sonnet-5) — validating the harness on a
fast backend before returning to Lemonade for local-inference bugs.
**Harness:** `C:\Users\14255\Work\gaia-tui-test\` (launch-tui.ps1 + driver.py), one
TUI at a time on control port 8817, launched `--dev --bypass-permissions`.

Architectural findings and v2 proposals live in
[`GAIA_AGENT_V2_SCOPE.md`](GAIA_AGENT_V2_SCOPE.md). This file is the test log.

---

## A correction I owe: the Claude backend was never blocked

I reported twice that `--use-claude` was blocked on a missing
`ANTHROPIC_API_KEY`. It was not. The key is in the repo's `.env` — which is
exactly where `ClaudeProvider` looks, because it calls `load_dotenv()` itself
(`src/gaia/llm/providers/claude.py`). I checked the User and Machine environment
scopes, found nothing, and stopped without checking the one place the code
actually reads. A 1.6s round trip to `claude-sonnet-5` settled it the moment I
looked properly.

---

## Session 2 — Claude backend

### Capability ladder — all seven rungs pass

| Rung | Result | Time |
|---|---|---|
| L1 arithmetic (`391`) | pass | 9.0s |
| L2 store memory | pass | 7.5s |
| L3 recall across turns (`Teal`) | pass | 4.5s |
| L4 shell tool (`pwd`) | pass | 67.5s with the permission prompt, ~8s bypassed |
| L5 load `github-triage` | pass | 12.0s |
| L6 skill persists across turns | pass | 3.0s |
| **L7 real `gh` triage** | **pass — exact match to `gh`** | **13.5s** |

Claude runs roughly 2x faster per turn than Gemma-4-E4B on this box, which is
what makes it the right backend for validating the harness: a failure is the
harness's, not the model running out of patience.

### Skill stress test — find, load, unload, execute

All four work now. Two of them did not when the session started.

| Check | Result |
|---|---|
| Discover a skill by need ("I need to work with a PDF") | pass — names `pdf`, states it is not loaded |
| Load a skill | pass |
| Load two at once | pass |
| Unload one, keep the other | pass |
| Report the capability as lost after unload | pass — "xlsx is unloaded, so I don't have that guidance active" |
| List every available skill | **was broken: 2 of 30. Fixed.** |
| Execute a skill end to end (build a PDF) | **was broken: three separate bugs. Fixed.** |

**Vanilla Claude Code skills load unmodified.** I installed Anthropic's `pdf`,
`xlsx`, `skill-creator` and `webapp-testing` from `anthropics/skills` into
`~/.gaia/skills/`. Their frontmatter carries only `name`/`description`/`license`
— no `metadata.gaia` block at all — and GAIA discovered and loaded all four.
That is the "can it load any SKILL" requirement met against skills GAIA has
never seen.

PDF execution verified against ground truth rather than the agent's word:

```
bytes: 602  header: b'%PDF-1.4'
contains text: True
```

### Memory stress test

Memory is the differentiator, so it got its own ladder. It **survives a process
restart**, which is the property that actually matters.

| Check | Result |
|---|---|
| M1 store four facts across categories in one turn | pass — four tool calls |
| M2 recall a specific fact | pass |
| M3 recall a different fact | pass |
| M4 correct a stored fact ("Beacon, not Halyard") | pass |
| M5 recall returns the correction, not the original | pass — no contradiction leak |
| **M6 recall after killing and respawning the agent** | **pass** |
| M7 recall across a different category (allergy) | pass |
| M8 stored style preference honoured in an unrelated answer | borderline pass — needs a tighter probe |
| M9 introspection: "what do you remember about me?" | pass, and **surfaced three problems** |

M9 turned out to be the most valuable test in the suite. What it exposed:

1. **The agent voluntarily remembers its own transient failures as durable
   notes.** It had stored "`execute_python_file` times out intermittently on
   reportlab PDF scripts" — true for about ten minutes, false now. The earlier
   fix stops the *auto-store* path persisting transient errors; it does nothing
   about the model calling `remember` on one itself.
2. **Secrets are stored in clear text and recited on request.** A passphrase
   from an earlier RAG probe came back verbatim in the list.
3. **An unexplained persona leaked in** — the answer opened "here's what I've
   got on you, Jordan-style rundown". Nothing in this session mentions Jordan.

All three are written up in `GAIA_AGENT_V2_SCOPE.md`.

### One oddity, not yet reproduced

A turn arrived as `▶ You: life Load the xlsx and github-triage skills.` — the
word "life" prepended to what the driver sent, on a fresh launch with an empty
composer. Recorded here so it is not lost; not filed until it reproduces.

---

## Bugs fixed this session

Every one of these was found by testing behaviour, and every one produced a
*confident wrong answer* rather than an error.

| Commit | What the user saw |
|---|---|
| `f6245a5b` | "The tool only surfaces 2 of the 30 skills per call" — a hardcoded 2 KB cap gutted every structured tool result |
| `05f5973e` | "Your script has a bug" — it didn't. A quoted `timeout: "120"` blew up inside `subprocess.run`; the same tool also had an stdin hang and a locale decode crash |
| `90704983` | A stray grey block hanging off the end of every line containing wrapped inline code |
| `9e66519d` | No tokens/sec in the dev footer — the token count was being dropped by the event translator |

## Delegated and in flight

| Task | Scope |
|---|---|
| `/model` switcher | Name the specific model in the header; switch between Claude and local models live; Lemonade version + health in dev mode |
| `/setup` | First-boot initialization via `gaia init`; re-runnable; skips model downloads under `--use-claude` |
| `/memory` | Read-only view of what the agent has stored |
| Competitive analysis | GAIA vs Hermes / OpenCLAW for long-horizon and autonomous work |
| Code-diff display | Claude-Code-style diffs on file edits |

---

## Session 1 — Lemonade backend (earlier)

### Where it stood

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

**6. The flagship could not find its own starter pack** — `db12be95`
`SKILL_DIRS` named only `gaia_agent/skills/`, which packaging stages but a
checkout leaves holding a lone `.gitkeep`. All nine starter skills were
unreachable without hand-copying them into `~/.gaia/skills`. Now falls back to
`hub/skills`, mirroring the `_MANIFEST_CANDIDATES` idiom beside it. Discovery
only — `default_skill_set` stays off and a test fails if it is quietly enabled.
5 new tests.

**7. `pwd` was labelled irreversible** — `457b8293`
The modal said "This is a destructive action and may not be reversible" for a
read-only `pwd`. `run_shell_command` is tiered Destructive because its *name*
does not bound it, not because the call is — so the unbounded case now says the
true thing and points at the command on screen. 2 new tests.

**8. One unmappable byte killed the system probes** — `f7ed527d`
The five sibling `text=True` call sites. `cmdkey /list` did not even catch
`UnicodeDecodeError`, so it took the whole credential-discovery pass down; the
three GPU probes swallowed it and returned nothing. An AST sweep now backs the
claim that none remain unguarded.

**9. `search_web` failed with a bare errno** — `48e288ae`
Node ships `npx` as `npx.cmd` on Windows, so the spawn failed with
"[WinError 2]" — and the agent turned that into "a consistent environmental
problem preventing external network requests". argv[0] now resolves through
`shutil.which`; a missing program names itself and points at Node; a missing
`PERPLEXITY_API_KEY` says so up front. 5 new tests.

**10. The agent had no voice, and no honesty floor** — `f5ecbc36`
New always-on `gaia-voice` skill (~900 tokens, no tools). Every rule is written
against an observed failure: substituting a near-miss and reporting success,
inventing causes for tool failures, narrating prompt-token counts at users,
refusing work it never attempted. Verified live — asked for a skill that does
not exist, it now says so and offers the ones that do.

**11. Fixed bugs were replayed into every prompt forever** — `72cb3037`
*(root cause of the intermittent skill-load failure)*
Tool errors are auto-stored as knowledge and injected into every later system
prompt under "Known errors to avoid". Nothing expired them. Long after the shell
hang was fixed, the prompt still carried *"run_shell_command: did not return
within 180s and was abandoned"* — so the model was told on every turn that shell
access was broken, which is exactly the story it kept telling users while running
those commands successfully. A session that recorded "No skill named
'github-triage'" before the skill was discoverable carried that belief forward,
which is the intermittent L5 failure. Transient errors are no longer persisted,
and a tool that succeeds retires the errors stored against it — which also
repairs databases already poisoned. 14 new tests.

**12. Test playbook updated** — `d64b8b42`
Six traps folded back into `.claude/skills/testing-the-gaia-agent/` so the harness
can run unattended: private agent log, installing `gaia-agent` with `--no-deps`,
the missing starter pack, `gh` needing the skill loaded, restarting Lemonade
correctly, and an ordered procedure for diagnosing the 180s shell hang.

---

## Open issues

**A. `--use-claude` is merged and built but unverified.**
Needs `ANTHROPIC_API_KEY`, absent in both User and Machine scope. The only
untested surface left.

**B. Lemonade is a single-slot single point of failure.**
It died outright twice mid-session (connection refused, no process). Restart is
`LemonadeServer.exe` — `lemonade-server serve` does not exist and `lemonade.exe`
is the client, which rejects `serve`. Not a GAIA defect, but it shapes every
timing measurement.

**C. Cold start is ~100s.**
First skill load in a fresh session is ~98s; warm is 12-14s. Honest, but the
spinner copy ("usually 60-90s") understates a cold first turn.

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
- **Skill loading** — 25/25 deterministic (in-process) and 6/6 live through the
  TUI, after the memory fix. Warm loads 12-14s.
- **Robustness sweep** — empty input is a no-op; idle Esc does not quit; cancel
  mid-generation takes **1.0s** (the playbook warned 60-90s); killing the agent
  mid-turn leaves the TUI alive and the next message respawns it.

---

## Corrections to my own earlier reads

Recording these because a wrong bug report costs more than a missing one.

- Guessed external executables hang when the agent's stdin is a pipe. **Wrong** —
  a direct repro ran `gh --version` in 0.08s in a daemon thread with piped stdin.
- Guessed the 180s timeouts were Lemonade contention and its eventual death.
  **Also wrong** — and this one I had already published as a correction, which
  made it worse. They reproduced with Lemonade healthy and warm. The process tree
  settled it: orphaned `gh.exe` processes whose parent `cmd.exe` was gone, which
  is a pipe deadlock, not contention. Two wrong theories before the evidence.
- Read `gofmt -l` output as real formatting drift. **Wrong** — CRLF artifact of a
  Windows checkout, present on files never touched.

---

## Next

1. `--use-claude` once `ANTHROPIC_API_KEY` is available, then re-validate on
   Lemonade.
2. Longer soak run now that the memory-poisoning cause is understood.

## Delivery note

Cannot push to `amd/gaia` — the authenticated account (`kovtcharov`) gets 403;
the PR branch lives on `amd/gaia`, not a fork. Work is pushed to `origin`
(`kovtcharov/gaia`) and raised as **draft PR amd/gaia#2964**, based on
`feat/gaia-flagship-agent-2804`.
