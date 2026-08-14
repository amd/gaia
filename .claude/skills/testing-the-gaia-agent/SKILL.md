---
name: testing-the-gaia-agent
description: Test the flagship GAIA agent end-to-end through the Go TUI — launch it, drive it without colliding with other agents, run the capability ladder, verify skills really call their tools instead of fabricating, and check the permission gate. Use when validating the gaia agent, its skills, or the TUI chat view.
---

# Testing the flagship GAIA agent through the TUI

Companion to `driving-the-tui` (which covers the control API mechanics). This one
covers **what to test and how to know it actually worked** — written from a full
session of driving the live agent, including every trap that cost an hour.

## The ladder tests capabilities. Users have conversations.

Read this before trusting a green ladder. Every rung below is a **self-contained
prompt** — it names its repo, its numbers, its subject. So the ladder ran green
for a whole day while the TUI agent had **no conversation history at all**: every
turn reached the model as system prompt + current question, nothing else. The
bug surfaced the moment a real user typed a follow-up:

> **triage amd/gaia** → three issues listed
> **"cool, can you print issue 2975?"** → *"I need to know which repository it belongs to"*

Three things hid it, and all three are worth knowing:

1. **L3 looks like proof of continuity and is not.** "What is my favourite
   colour?" passes across turns via the persistent *memory store*, a different
   mechanism entirely. Its green tick actively masked the gap.
2. **The stdio test asserting turn-to-turn state passes for the wrong reason.**
   `test_the_agent_survives_between_turns` asserts OBJECT state
   (`agent.loaded_skills`) survives — it does, the agent is the same object.
   History is not accumulated object state; nobody was appending to it.
3. **The HTTP surface populates history, so any test at that layer passes.** The
   defect was transport-specific, and only the TUI used the broken transport.

**So always finish with a follow-up that cannot stand alone.** Use a pronoun or
a bare number and give it nothing else:

| after | ask | pass condition |
|---|---|---|
| a triage of amd/gaia | `cool, can you print issue 2975?` | prints it, never asks which repo |
| `My favourite fruit is mango. Just acknowledge.` | `What fruit did I just mention? One word.` | `Mango` |

The second pair is the cheap canary — two short turns, no tools, no network. Run
it first. If it answers "no fruit has been mentioned", stop: history is broken
and every other result is measuring an agent with amnesia.

## The one rule

**A plausible answer is not a passing test.** The flagship's worst failure mode is
answering confidently when its tools are missing. It once produced a polished
"here's how I'd triage that" paragraph while having *zero* GitHub tools registered.
Every capability claim must be checked against ground truth from outside the agent:

```bash
# agent said: #2958, #2955, #2953
gh issue list --repo amd/gaia --limit 3 --json number,title   # must match exactly
```

If you cannot independently verify a result, report it as unverified. Say so plainly.

## Two rules about the machine — ignore these and you will measure noise

Both of these cost real hours in the session this skill came from, and both produce
symptoms that look like product bugs.

### 1. Exactly ONE TUI at a time

Kill every existing instance before launching, and never leave a second one running:

```bash
# Windows
for p in $(tasklist //FI "IMAGENAME eq gaia-drive.exe" //FO CSV //NH | cut -d, -f2 | tr -d '"'); do
  taskkill //PID $p //F
done
```

Two TUIs is not merely wasteful:

- They **overwrite each other's `~/.gaia/tui/control.json`** — same pid/port/token file —
  so your driver silently attaches to whichever launched last. A query you never sent
  appears in your transcript; keys you send land in someone else's session. This happened
  in both directions in one day, and each time looked like a TUI bug.
- Each spawns its own agent child, so they **compete for the model** and every turn slows.
- The user is memory-constrained; two instances is a real cost, not a rounding error.

`GAIA_TUI_HOME` isolates the *discovery file* so concurrent agents stop hijacking each
other — it does **not** remove the model contention. One TUI, always.

### 2. Never run an eval while testing the agent

`gaia eval agent` and the TUI both drive the **single-slot** Lemonade backend. Running
them together makes every turn 2-5x slower and the slowdown reads as "the agent is
extremely slow" — a product complaint caused entirely by the harness. Measured on the
same box, same build:

| | with an eval running | box quiet |
|---|---|---|
| load a skill | 74s | **13.5s** |
| real `gh` triage | (unusable) | **27s** |

Worse, CLAUDE.md warns that concurrent runs race the model slot and can produce
chaotic, meaningless failures (`BLOCKED_BY_ARCHITECTURE`, `INFRA_ERROR`, ctx-size
errors) that get mistaken for regressions.

**Check before you start, and check again when things feel slow:**

```bash
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object -ExpandProperty CommandLine" | grep -iE "eval agent|ui.server"
```

Evals are a **pre-merge gate, not a testing-session activity**. When a change requires
one (CLAUDE.md lists the surfaces — prompts, tool schemas, tool-call parsing), record it
as outstanding and run it when the box is quiet and nobody is driving the TUI. Never run
two evals at once, either.

### 3. Give your session a private agent log

Every agent appends to `~/.gaia/logs/gaia-agent.log`. When anything else is running an
agent — a parallel task, a second harness — the file interleaves, and **a neighbour's
tool timeout reads as your session's failure**. Set `GAIA_AGENT_LOG` in the launcher:

```powershell
$env:GAIA_AGENT_LOG = 'C:\...\gaia-tui-test\logs\agent-session.log'
```

Lines also carry `pid:NNNN`, so the shared default is still attributable when you forget.
This is not hypothetical: a 180s `run_shell_command` timeout was nearly filed as a shell
bug here before the record turned out to belong to another process. Confirm the pid in
the log matches the `gaia-agent.exe` your TUI spawned before believing anything.

## Which surface you are testing

One binary, two surfaces — always state which:

| command | surface |
|---|---|
| `gaia-drive.exe` (bare) | Agent **Hub** browser — install/launch agents |
| `gaia-drive.exe run gaia` | **flagship chat view** — where skills load |

Launching bare and typing lands your text in the Hub's filter box, not a chat
composer. That produced a fake bug report once.

## Setup

### 1. Build

```bash
cd tui && go build -o bin/gaia-drive.exe ./cmd/gaia
```

Do not launch while a build is writing the binary — the file lock makes the launch
silently fail. Build, *then* launch.

Go must be on PATH (`export PATH="/c/Program Files/Go/bin:$PATH"` on Windows). Note that
`gofmt -l` flags nearly every file on a Windows checkout — that is CRLF, not real
formatting drift. Check `gofmt -d <file> | cat -A` for `^M` before you "fix" anything.

### 1b. The agent binary the TUI spawns

The TUI launches `gaia-agent` **from PATH** (`catalog.go`, `BinaryPath`). A source
checkout does not have it — the console script only exists once the hub package is
installed:

```bash
uv pip install --python <venv>/Scripts/python.exe \
  -e hub/agents/gaia/python -e hub/agents/chat/python --no-deps
```

`--no-deps` is mandatory: without it pip pulls `amd-gaia` from PyPI and the agent
imports THAT instead of your worktree. Put the venv's `Scripts/` on PATH in the launcher
or the TUI cannot find `gaia-agent`.

### 1c. The flagship ships with NO skills

`gaia_agent/skills/` holds only `.gitkeep`, nothing stages `hub/skills/` into it, and
every `skills:` / `skill_sets:` / `default_skill_set:` key in `gaia-agent.yaml` is
commented out. So **L5–L7 cannot pass on a clean checkout** — not because the agent is
broken, but because it has nothing to load.

Install the one you are testing, and copy it rather than `gaia skill import` — import
re-stamps the tier `experimental`, which is not what ships:

```bash
cp -r hub/skills/github-triage ~/.gaia/skills/
gaia skill list      # expect: github-triage  2.0.0  community  user
```

Also note `gh` is refused until the skill that grants it is **loaded** — the grant is
`shell:execute:gh`. Asking for `gh` first produces a confident refusal that looks like a
missing-tool bug and is not one.

### 2. Launcher (adapt paths, keep the structure)

Create `launch-tui.ps1`. Every line matters:

```powershell
$root = '<ABSOLUTE PATH TO YOUR WORKTREE>'
$env:PYTHONPATH = "$root\src;$root\hub\agents\chat\python;$root\hub\agents\gaia\python"
$env:GAIA_TUI_HOME = '<A PRIVATE TEMP DIR — NOT ~/.gaia/tui>'
$env:PYTHONIOENCODING = 'utf-8'
$inner = "cd /d `"$root`" && tui\bin\gaia-drive.exe run gaia --control-port 8817"
Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', $inner -WindowStyle Normal
```

Launch with:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <path>\launch-tui.ps1
```

**`PYTHONPATH` is mandatory.** An editable install can resolve `gaia` to a *different*
worktree, and the agent then dies at import with
`ModuleNotFoundError: No module named 'gaia.ui.sse_translation'`. Verify:

```bash
python -c "import gaia; print(gaia.__file__)"   # must be YOUR worktree
```

**`GAIA_TUI_HOME` is mandatory when other agents may be running** — see machine rule 1
above. It gives you a private `control.json` (`tui/internal/control/paths.go`) instead
of the shared `~/.gaia/tui/control.json` that agents hijack from each other. It does not
excuse running two TUIs.

**Do not use `cmd //c start` from Git Bash** — MSYS mangles the arguments and no window
opens. PowerShell `Start-Process` with a `.ps1` avoids the quoting entirely.

### 3. Driver

Use `driver.py` from this skill directory (repoint `CJ` at your `GAIA_TUI_HOME`).

**Why one process:** process spawn costs 0.7–2.0s on a Windows/MSYS box with AV —
`curl --version` alone measured 2051 ms. A bash driver spawning bash + 2 × python +
curl per command cost ~4.8s per call. The control API itself is **3 ms**. Batch every
step of a test into ONE python process:

```
5 control calls in one process: 15 ms total
```

## Driving correctly

- **Wait for `streaming:true` BEFORE waiting for `streaming:false`.** Otherwise the
  idle-wait matches the *pre-turn* idle state and returns in 0.0s, and you will report
  a phantom instant answer.
- **Press `end` before every capture** or you capture stale scrollback and read an old
  turn as the current one.
- **Never `sleep`** to wait out a turn — poll status or use `/control/v1/wait`.
- **Set `PYTHONIOENCODING=utf-8`** or captures die on `cp1252` for the spinner glyphs.
- **Do not resize larger than the real terminal** — the control API returns 409
  `resize_exceeds_terminal`; a bigger size shreds the frame.

## The capability ladder

Run in order. Stop and diagnose at the first failure — later rungs depend on earlier.

| # | prompt | pass condition | ref time |
|---|---|---|---|
| L1 | `What is 17 times 23? Answer with just the number.` | `391` | ~20s |
| L2 | `Remember that my favourite colour is teal. Just acknowledge.` | acknowledges | ~22s |
| L3 | `What is my favourite colour? One word.` | `Teal` — memory crosses turns | ~22s |
| L4 | `Use your shell tool to run pwd and tell me the directory.` | runs, or prompts and runs on approval | varies |
| L5 | `Load the github-triage skill.` | loads | ~14s |
| L6 | `Which skills do you currently have loaded? Name them.` | names it — **skill survives the turn** | ~12s |
| L7 | `Using the github-triage skill, list the 3 most recently opened issues in amd/gaia.` | real numbers+titles matching `gh` | ~27s |

L6 is the regression canary for a bug where the skill vanished between turns.
L7 is the real test: it fails *silently* by producing a confident non-answer.

### Diagnosing L7 failure

If it deflects ("first configure the connector…") it has no tools. Check, in order:

```bash
# 1. Does it think it has tools?  (a NONE here is the smoking gun)
#    ask in the TUI: "List the exact names of every tool you can call that talks
#    to GitHub. If you have none, say NONE."

# 2. What did the loader actually register?
grep -E "Loaded skill|registered_tools" ~/.gaia/logs/gaia-agent.log | tail -5
#    "0 tool(s), 1 connector requirement(s)" + 'registered_tools': [] == no tools

# 3. Is the skill the version you think?
grep -E "version:|shell:execute|mcp:connect" ~/.gaia/skills/github-triage/SKILL.md
```

**The installed copy at `~/.gaia/skills/<name>/SKILL.md` is what the agent reads**, not
the repo copy. After editing the repo skill, sync it or the agent runs the old one.

## Verifying the permission gate

The `gh` grant is read-only. Test the gate directly — it is instant and needs no LLM:

```bash
python -c "
from gaia.skills.binaries import BINARY_POLICIES, validate_invocation
p = BINARY_POLICIES['gh']
for cmd in ['gh issue list --repo amd/gaia', 'gh auth status', 'gh auth token',
            'gh issue create --title x', 'gh api -X POST /repos', 'gh alias set x !sh',
            'gh extension install evil', 'gh api repos/amd/gaia/issues']:
    err = validate_invocation(p, cmd.split())
    print(f'{cmd:34} -> ' + ('ALLOWED' if err is None else 'REFUSED'))
"
```

Expected — only the first, second and last are ALLOWED:

```
gh issue list --repo amd/gaia      -> ALLOWED
gh auth status                     -> ALLOWED
gh auth token                      -> REFUSED     <- prints the credential
gh issue create --title x          -> REFUSED
gh api -X POST /repos              -> REFUSED     <- -X may only be GET
gh alias set x !sh                 -> REFUSED     <- defines arbitrary shell
gh extension install evil          -> REFUSED     <- installs and runs code
gh api repos/amd/gaia/issues       -> ALLOWED
```

Then confirm end-to-end in the TUI that a write is refused *in prose*, e.g.
`Use the gh CLI to create a new issue in amd/gaia titled "test issue please ignore".`

## Measuring streaming

Sample on-screen character count during a turn. Rising = streaming; one jump at the
end = not.

**Confound to avoid:** total screen chars include scrollback, and a re-render can make
the count *drop*. Scope the count to the current answer region (text after the last
`▶ You:` line), or scroll to a clean state first. A naive whole-screen count produced
an unreadable series (`1301 … 1437, 991`) and proved nothing.

## Robustness checks

| check | how | expected |
|---|---|---|
| empty input | Enter on empty composer | no-op, no phantom turn |
| agent crash | `taskkill /PID <gaia-agent.exe pid> /F` mid-turn | TUI survives, shows the exit, respawns next turn |
| cancel between steps | Esc early in a turn | cancels < 2s, transcript intact |
| cancel mid-generation | Esc during a long answer | **can take 60–90s** — cooperative, only checked at step boundaries |
| idle Esc | Esc with nothing streaming | must NOT quit silently |

## Known-good baselines (Gemma-4-E4B, GPU, quiet box)

| operation | time |
|---|---|
| trivial turn | ~20s |
| load a skill | ~13s |
| real `gh` triage | ~27s |
| agent cold start | ~16–19s |

**If everything is 2–5× slower, suspect the harness before the product** — a stray eval
or a second TUI, per the two machine rules above. Confirm the backend is actually up
and on the right port:

```bash
curl -s http://127.0.0.1:13305/api/v1/health    # note: 13305, NOT 8000
```

Lemonade has died on its own mid-session more than once. Check it before blaming a
change. Restarting it is **not** `lemonade-server serve` — that binary may not exist,
and `lemonade.exe` is the *client* and rejects `serve`:

```bash
powershell.exe -NoProfile -Command "Start-Process 'C:\Users\<you>\AppData\Local\lemonade_server\bin\LemonadeServer.exe' -WindowStyle Minimized"
curl -s -X POST http://127.0.0.1:13305/api/v1/load -H "Content-Type: application/json" \
     -d '{"model_name":"Gemma-4-E4B-it-GGUF"}'      # pre-warm, or turn 1 pays ~3.5 min
```

A cold first turn is **~240s** (ttft ~228s) while both the LLM and the embedding model
load; warm turns are ~6s. Pre-warm before timing anything, or the first number is a
model load and you will report it as agent latency.

## When a shell command hangs for exactly 180s

Two real bugs produced this, both fixed — but the diagnostic pattern generalises to any
tool the agent shells out to.

1. **Check for orphans.** `Get-CimInstance Win32_Process -Filter "Name='gh.exe'"`. A
   live child whose parent is gone means `subprocess.run` killed the `cmd.exe` at its
   inner timeout, then blocked forever in a second `communicate()` on pipes the
   grandchild still holds. The 180s you see is the OUTER tool timeout.
2. **Compare against the same command from a shell.** 0.07s outside vs a hang inside
   means the environment the agent spawns into, not the command.
3. **Suspect stdin first.** `capture_output` redirects stdout/stderr and leaves stdin
   inherited — the agent's stdin is the TUI's pipe, open and never written. Anything
   that reads or probes it waits on input that cannot arrive.
4. **Then suspect the decode.** Bare `text=True` decodes with the OS locale codec
   (cp1252 on Windows) *inside subprocess's reader thread*. One unmappable byte kills
   that thread and `run()` returns **returncode 0 with empty stdout** — a success with
   the output silently discarded. `gh issue list` on amd/gaia hits it, because issue
   #2962's title contains "⚠️".

Both failures lie in the same direction: the agent reports a confident, wrong
explanation ("a networking bottleneck", "no issues found") rather than an error. Always
diff the agent's answer against `gh` directly.

## Reporting

Per [CLAUDE.md → How You Communicate](../../../CLAUDE.md#how-you-communicate): open with
whether it works, in one plain sentence, then captures and detail beneath.

Specific to this skill:

- **Paste real captured text, never paraphrase.** A paraphrased frame hides the bug.
- **State every rung you did not reach.** An unstated gap reads as a pass.
- **Verify before attributing a bug to your change.** The tree often has other agents'
  uncommitted work — `git status` / `git diff` first. A "broken build" once turned out
  to be a stale test cache; a suspected regression turned out to be a rendering-only
  diff.
- **Correct yourself out loud.** A wrong bug report costs more than a missing one.
