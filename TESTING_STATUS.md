# GAIA agent + TUI — testing status

Live log of validating the flagship agent through the Go TUI on branch
`feat/gaia-flagship-agent-2804` (PR #2932). Updated continuously.

**Last updated:** 2026-08-14 09:30
**Backend:** both. Claude (`--use-claude`, claude-sonnet-5) validated the harness;
the suite has since been re-run on local Gemma-4-E4B via Lemonade.
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

## Final pass on the merged build

Everything below ran against the branch with all four delegated features merged
(`/model`, `/setup`, `/memory`, plus my own changes).

### Capability ladder — 7/7, and about twice as fast as the pre-merge run

| Rung | Result | Time |
|---|---|---|
| L1 arithmetic (`391`) | pass | 4.5s |
| L2 store memory | pass | 7.5s |
| L3 recall across turns (`Teal`) | pass | 4.5s |
| L4 shell tool (`pwd`) | pass | 7.5s |
| L5 load `github-triage` | pass | 9.0s |
| L6 skill persists across turns | pass | 3.0s |
| **L7 real `gh` triage** | **pass — exact match** | **10.5s** |

L7 verified against ground truth rather than accepted:

```
$ gh issue list --repo amd/gaia --limit 3
2975 — fix(ci): the README CI badge is permanently red and reports nothing
2974 — docs(email): hub page tells users to run a nonexistent command
2973 — perf(deps): amd-gaia pulls full NVIDIA CUDA stack on AMD hardware
```

### The header now names the model, and the local server

```
 GAIA  │ dev │ Sonnet 5 │ lemonade 10.10.0
```

`/model` lists Claude models and the real Lemonade catalog discovered at
runtime, current one marked, remote and local visually distinct. Switching live
from Sonnet 5 to `Gemma-4-E4B-it-GGUF` took ~1s and — the part that matters —
**conversation history survived it**: a codename given to Claude was recalled by
the local model on the next turn.

### Self-critique: measured, and it helped

Run against a question with known ground truth, where memory held a stale claim.

| | Steps | Tools | Wall clock | ttft | Answer |
|---|---|---|---|---|---|
| No critique | 1 | 0 | 13.0s | 11.8s | **wrong** — recited stale memory, verified nothing |
| With critique | 4 | 3 | **11.2s** | **2.2s** | **right** — ran a test, corrected itself out loud |

More accurate *and not slower*: it traded generation time for verification.
Caveats and the exact wording that worked are in `GAIA_AGENT_V2_SCOPE.md` §4.6.

### The merge broke Enter, and only the ladder caught it

Merging the four feature branches took the ladder from 7/7 to **0/7**. Nothing
errored: `go test ./...` was green, and every rung simply timed out at 20s with
the startup banner as its captured output, because no turn ever started.

One branch had added a heuristic treating an Enter arriving within 50ms of the
last keystroke as a terminal typing out a paste rather than a person pressing
send. Windows Terminal over ConPTY really does type pastes out that way, so the
intent was sound — but the signal is not specific to pasting. It also fires for a
fast typist, and for the control API, which delivers a line and its Enter back to
back.

Reverted to the authoritative signal (the message's own bracketed-paste flag).
On terminals that do not bracket, a multi-line paste sends its first line — now
pinned by a test that says so — and Ctrl+V reads the clipboard directly, which is
the path that works there. Ladder back to 7/7.

**The lesson is in the test skill now:** the ladder is the merge gate, not just
the feature gate, and any input rule of the form "too fast to be a person" will
find your harness.

### The stray characters were my harness, not GAIA

I reported turns arriving with junk prepended (`life`, `t`, `e's`) and was
building a case for an input bug. It reproduced 2 of 4 launches, then 0 of 3 —
and the fragments are pieces of text the user was typing at the time. The
launcher uses `Start-Process -WindowStyle Normal`, which steals focus, so the
new window catches a few keystrokes meant for another app. A harness artifact.
Recorded here because a phantom bug filed against the product costs more than
the hour it took to disprove.

---

## Round 3 — ten popular skills, tested on their output

Every skill below was given a real task and its output checked against ground
truth with an independent library, not taken from the agent's word. Their Python
dependencies were installed first (`reportlab`, `openpyxl`, `python-docx`,
`playwright`) so each ran the way its author intended.

| # | Skill | Task | Verified by | Result |
|---|---|---|---|---|
| 1 | `docx` | Heading 1 + a 2-sentence paragraph + 3 bullets | `python-docx` | **pass** — exactly 2 sentences, exactly 3 `List Bullet` items |
| 2 | `pptx` | 3 slides, given titles, 2 bullets each | `python-pptx` | **pass** — 3 slides, titles and bullets exact |
| 3 | `pdf` | 2-page PDF, then read it back | `pypdf` | **pass** — 2 pages, headings correct, its read-back matched the file |
| 4 | `xlsx` | formulas + bold header + grand total | `openpyxl` | **pass** — real `=B2*C2` / `=SUM(D2:D5)`, header bold, stated total 8800 matches |
| 5 | `skill-creator` | author a new `changelog-writer` skill | GAIA's own loader | **pass** — valid SKILL.md, then loaded and used to produce a real changelog |
| 6 | `mcp-builder` | a working MCP server | Python `ast` | **pass** — valid FastMCP, decorated tool, typed signature |
| 7 | `web-artifacts-builder` | self-contained HTML bar chart | regex over the file | **pass** — no external refs at all, every data point present |
| 8 | `webapp-testing` | drive that page with Playwright | the PNG + the DOM | **pass** — correct heading, 4 bars, valid 1280x720 screenshot |
| 9 | `internal-comms` | a deploy-freeze announcement | judgement | **pass** — short, complete, actionable |
| 10 | `brand-guidelines` | name its rules, then apply them | the SKILL.md itself | **pass** — every fact verbatim-correct, and it refused to overclaim |

**#10 is the most interesting result.** The skill turned out to be a *visual*
brand guide, not a copy style guide. The agent said so, listed its real rules
(Poppins/Lora, the exact hex palette — all verified against the file), rewrote
the sentence anyway, and then explicitly refused to credit the skill for it:
*"I can't honestly claim the skill drove this rewrite — that's just plain
editing."* That is the honesty floor doing exactly its job.

**#5 is the strongest capability result:** skill-creator authored a skill, GAIA
loaded it, and used it to produce a correctly-grouped changelog — then pushed
back that a `BREAKING CHANGE` implies 3.0.0 rather than the 2.1.0 it was given.

One real limitation found: **the agent cannot install a skill it just wrote.**
`~/.gaia/skills` is write-protected from the agent, so skill-creator's output
has to be moved by hand. It said so plainly rather than failing quietly.

## Bugs found and fixed in round 3

| Commit | What you'd have seen |
|---|---|
| `e423537a` | "xlsx installed but not shown in this listing due to truncation" — a 200K-context model held to the local NPU's 20K budget |
| `14164171` | **A multi-line question arrived as several separate questions.** Five pasted commits produced a changelog of the first line, with the agent insisting that was all it had been sent |
| `8c0a691a` | **A full `gaia init` — including a vite production build — ran on every launch** of an already-working machine |
| `f59320a9` | **`/help` did nothing at all** in a `gaia run <agent>` session, silently |
| `1eef2c3c`, `f442e1d5` | A stored passphrase was recited on request, and sent to Anthropic on a Claude session |

The multi-line one is the most consequential: it looked exactly like the model
ignoring half the message. It was the transport splitting the question at every
newline, because the agent reads stdin a line at a time.

Two of these only appear away from a dev box. `gaia init --check` is newer than
the released CLI, so an installed `gaia` exits 2 with "unrecognized arguments" —
which the gate read as "clean machine". And `/help` worked from the hub, which
is the path nobody launches the flagship from.

---

## Round 4 — the same tests on local Gemma-4-E4B via Lemonade

The point of this round: **every bug below was invisible on Claude.** The
stronger model papered over gaps the local one falls straight into, and the local
one is the product's actual target.

### Ladder — 7/7, and the tokens/sec figure is finally real

| Rung | Result | Time |
|---|---|---|
| L1 arithmetic | pass | 4.5s |
| L2 store memory | pass | 18.0s |
| L3 recall across turns | pass | 24.0s |
| L4 shell tool | pass | 18.0s |
| L5 load `github-triage` | pass | 57.0s |
| L6 skill persists | pass | 7.5s |
| **L7 real `gh` triage** | **pass — exact match** | **75.1s** |

L7 reported `44.4 tok/s`, which is what this hardware actually does — the fix for
the fabricated rate is confirmed against a real streaming turn, and correctly
withheld on the turns that did not stream.

### Documents — all three verified, after three separate fixes

| Skill | Verified with | Result |
|---|---|---|
| `docx` | `python-docx` | Heading 1 + paragraph, 36KB, correct |
| `xlsx` | `openpyxl` | headers, both rows, real `=SUM(B2:B3)` |
| `pdf` | `pypdf` | valid 1-page PDF, correct heading |

Each of those failed first, in a different way — see below.

### Memory holds up locally

Stored two facts, killed the agent, relaunched, and asked: *"The deployment
target codename is FALCON, and you work out of the Sydney office."* Credential
masking works here too — the stored passphrase came back as *"a flagged entry
that appears to be a credential or key; please use /memory"*, with no value.

### Switching backends works both ways

`/model claude-sonnet-5` from a local session switched in 17.6s, updated the
header to `Sonnet 5 │ lemonade 10.10.0`, and said plainly that the conversation
now goes to Anthropic.

---

## Bugs found on the local model

Every one of these produced a confident, well-formed answer.

**It claimed to have written a file it never created.** Asked for a Word
document: *"The .docx file has been created at …, containing your requested
heading and paragraph."* No such file existed — it had written a Python script
that *would* create it and stopped there. A plan reported as an outcome.

**Before that, it refused outright** — *"the docx skill isn't currently available
to me"* — with `docx` installed, one `load_skill` call away, in a library of 36.
Not being loaded is not the same as not existing, and it cannot tell the
difference from memory.

**It produced a PDF no reader could open** — 236 bytes, no EOF marker — and
reported success. The cause is the interesting part: it tried to run
`scripts/reportlab_creator.py`, a path the `pdf` skill really does ship, which
resolves against the process's working directory and therefore nowhere.
`load_skill` never said where the skill lived. Having found nothing, it fell back
to hand-writing raw PDF — **a fallback it had learned and stored in memory during
an earlier session**, and which is now wrong because reportlab is installed.

That last one is the clearest evidence yet for the memory-trust work: a stale
procedural memory did not just mislead an answer, it caused a corrupt artifact.

**And the tokens/sec guard was still too weak.** A 24.8s turn whose first token
arrived at 23.6s cleared the one-second floor on its 1.2s remainder and published
`556.6 tok/s` for a model that runs at about 44.

| Commit | Fix |
|---|---|
| `e5ce4bbd` | "Writing the script is not doing the work" + check `list_skills` before claiming a skill is missing; tokens/sec now needs a real share of the turn |
| `86b763f6` | `load_skill` returns the skill's directory, so bundled scripts resolve |
| `7ffad5e4` | `openpyxl` and `reportlab` ship with the chat profile |

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
| `58b6066a` | Thirty skill names as one comma-run that wrapped mid-word |
| `96639846` | Typing three follow-ups during a turn kept the third and silently dropped two |
| `396f41a7` | "2142.6 tok/s" on a local Gemma — a rate invented from a 0.1s window |
| `a460a3af` | Dev header said nothing about Lemonade's version or health |
| `9b203eb2` | **Enter stopped submitting entirely** after the merge — a paste heuristic read a fast Enter as a line break |

## Delegated and in flight

| Task | Scope |
|---|---|
| `/model` switcher | **merged** — header names the model, live switching works, history survives it |
| `/setup` | **merged** — first-boot init, re-runnable, skips model downloads under `--use-claude` |
| `/memory` | Read-only view of what the agent has stored — **merged** |
| Competitive analysis | GAIA vs Hermes / OpenCLAW — **delivered** |
| Clipboard paste | Diagnosing why Ctrl+V does not paste — in flight |
| Code-diff display | Claude-Code-style diffs on file edits — separate PR |

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
