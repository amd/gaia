# GAIA packaging and first run — one binary, one front door

Status: Design / approved in principle, not started
Extends: [`docs/plans/tui-user-journey.md`](../../plans/tui-user-journey.md) — that document
covers stages 1–11 of the user journey and assumes `gaia` is already running. This document
covers **stage 0**: how `gaia` gets onto a machine at all, and what the machine looks like
afterwards. It does not replace it.
Related: [`docs/plans/agent-factory.md`](../../plans/agent-factory.md) (the developer flow,
specced separately), [`docs/plans/package-publishing.mdx`](../../plans/package-publishing.mdx)
(the registry-driven publisher this uses), [`docs/plans/desktop-installer.mdx`](../../plans/desktop-installer.mdx)
(the installers this supersedes in part).

---

## 0. The finding

**GAIA has no front door.** It has seven of them, and none of them works on a blank machine
without a second command the user has to already know. There are 36 top-level CLI commands,
9 distinct executables, and two different programs both named `gaia`. The one-line install
the website advertises — `curl -fsSL https://amd-gaia.ai/install.sh | sh` — returns 404,
because the script exists at `installer/scripts/install.sh` and was never published to
`website/public/`.

The fix is not another installer. It is to make **one small program the only thing a user
ever installs**, and to make everything else something that program fetches, verifies, and
manages. That program exists already — the Go CLI/TUI in `tui/` — and it is built by CI six
times per commit and thrown away.

Two things make this tractable now that would not have been a year ago. **Agents are already
compiled binaries**: `release_agent_email.yml` freezes each agent per-platform, publishes it
to the hub, and verifies the hash — no interpreter required on the target. And **the hub is
already a verified artifact store** readable by a dumb client: plain JSON over HTTPS at
`{hub}/index.json` and `{hub}/agents/<id>/manifest.json` (`src/gaia/hub/catalog.py:41-86`),
with per-platform `{url, sha256}` entries. Everything below is an application of those two
facts.

---

## 1. What a user's machine looks like

Three things, and nothing else:

| Thing | Size | Arrives when |
|---|---|---|
| `gaia` — the one program | ~16–17 MB | you install it |
| each agent | ~40–90 MB | when you install that agent |
| Lemonade Server + a model | ~4 GB | only if an agent needs local inference, and only on an explicit keypress |

**No Python. No virtual environment. No `pip`. No `gaia init` to remember.**

This is a change from the status quo in kind, not degree. Today a user gets a 1.1 GB Python
environment before they can do anything, `transformers` and `accelerate` are core
dependencies (`setup.py:59-60`) and `accelerate` requires torch — so the ~372 MB of ML stack
that GAIA never runs in-process is not optional today, it is mandatory. Inference is HTTP to
Lemonade Server. Nothing on the user path imports torch. The email agent's freeze config
records this explicitly:

> "Real triage talks to the local Lemonade Server over HTTP and never runs torch in-process,
> so we EXCLUDE the ML stack to keep the binary lean (~90 MB vs ~2 GB)."
> — `hub/agents/email/python/packaging/freeze.py`

### 1.1 There is no engine

An earlier draft of this design shipped a compiled Python "engine" (~73 MB measured — see
§9.1) carrying the daemon, the setup logic, the hub installer, and connectors. **That is
rejected.** Nothing that layer does requires Python: it supervises processes, stores
credentials, performs OAuth, downloads files, and calls HTTP APIs. It is Python because that
is where it was written first.

**The management layer moves into the Go binary.** The user's machine never receives a Python
runtime for any reason.

**A compiled component is not a runtime.** §2.1 permits `gaia` to invoke a *frozen* binary that
happens to have been written in Python — that is what every agent already is. The rule being
set here is narrower and firmer than "no Python": **nothing arrives on a user's machine that
can execute arbitrary Python, and nothing arrives that needs an interpreter to run.** A frozen
component is subject to the same fetch, hash-verify and sentinel treatment as an agent, and to
the same consent rule.

This is the largest single piece of work in this design and §7 sequences it so that nothing
else waits on it.

### 1.2 Installing `gaia` installs nothing else

**Installing the program is not installing GAIA's agents.** No channel — `curl | sh`, brew,
winget, apt, or a desktop installer — may pre-install an agent, a model, or Lemonade. A fresh
install is ~16–17 MB and one program, full stop.

(Release builds are stripped — `build_tui.yml` builds with `-s -w` and its size-check warns
above 15 MB; a stripped darwin/arm64 build measured 16.5 MB. An unstripped local `make build`
is larger and is not what ships.)

Agents arrive only when the user asks for one, through the TUI or the CLI. This is the
difference between a tool you can try and a tool you have to commit to, and it is the single
most important property of the install for a first-time user.

### 1.3 The consent rule: nothing large downloads without a yes

**Every download beyond the program itself states its size and its source, and waits for an
explicit keypress.** No exceptions, and no download begins as a side effect of another action.

| Download | Consent given by |
|---|---|
| the `gaia` program | the install command the user ran |
| an agent (~40–90 MB) | pressing install, after the size is shown |
| Lemonade + a model (~4 GB) | pressing the fix key, after the size is shown |
| a component update | pressing update, after the size is shown |

Three consequences that are easy to get wrong and must be built in from the start:

1. **A readiness check never downloads.** It reads state and reports. Repair is a separate
   keypress. Note that `tui/internal/ui/preflight/provision.go` already pulls a model — correct
   for a fix key, wrong for a check (§4.3).
2. **Sizes come from the manifest, not from guesses.** The artifact manifest carries a real
   byte count per platform, so the number on screen is the number that downloads. This is
   currently broken: `binaries.lock.json` ships `"size": 0` for all four platforms (§9.6).
   Publishing a real size is a prerequisite for the install screen, not a nicety.
3. **A download in progress is interruptible and reports what it left behind.** "Nothing was
   installed; nothing to clean up" is a sentence the user has to be able to trust
   (`docs/plans/tui-user-journey.md` §3 Stage 2).

The rule extends past first run. A user who has used GAIA for months and asks it to check the
setup must never discover that asking the question started a 4 GB transfer.

---

## 2. What the Go binary owns

Exactly the capabilities that touch compiled agents or the hub:

1. Browse and search the agent catalog
2. Install / uninstall an agent
3. Run an agent — interactive session or one-shot query
4. Supervise agents — start, stop, status, logs
5. **Set up the machine** — what `gaia init` does today: install Lemonade, download a model,
   verify the result — plus check and repair it on demand
6. Update itself and its components

**Explicitly not in it:** evaluations and scorecards, agent authoring and publishing, reports
and performance plots, test harnesses, RAG and document tooling, the web UI, the API server.

**And not the per-agent commands.** `gaia email`, `gaia jira`, `gaia docker`, `gaia sd`,
`gaia blender`, `gaia summarize`, `gaia browse`, `gaia analyze` are agents wearing a CLI
costume. Every one already exists as a hub agent under `hub/agents/<id>/`. They are not ported
— they are removed from the user's view, because `gaia run <id>` covers them. This is where
most of the 36-command surface goes, and it goes without anything being reimplemented.

### 2.1 One capability, one owner, one implementation

**Nothing is implemented twice.** A capability belongs to exactly one of the two CLIs, and
after this work no capability appears in both. The split is by *audience*, not by language:
user capabilities live in `gaia`, developer capabilities live in `gaia-dev`.

Where a user capability already exists in Python, there are two dispositions and a test for
choosing between them:

| Disposition | When | Consequence |
|---|---|---|
| **Port to Go** | most of the Python is terminal UI the TUI replaces anyway | removed from `gaia-dev` |
| **Invoke as a component** | it is genuine domain logic with little UI, and reimplementing would duplicate hard-won behaviour | shipped as a compiled component `gaia` drives — **not** a user command and **not** a `gaia-dev` command |

The second disposition is the owner's "call it rather than reimplement it," made compatible
with §1's no-Python-runtime rule: what `gaia` invokes is a **compiled binary fetched and
verified like an agent**, not an interpreter. If something is worth calling rather than
porting, it is worth freezing.

Either way the capability ends up in one place. A user capability that stays reachable from
`gaia-dev` has not been separated; it has been copied.

**`gaia init` is a port, not a component.** It is a user flow and belongs in `gaia`. Of its
2,251 lines (`src/gaia/installer/init_command.py`), the large majority is terminal printing,
interactive prompts, and profile bookkeeping — all of which the readiness ladder replaces. The
irreducible installer logic is an estimated 550–700 lines of Go, and roughly 230 lines of
launcher resolution already exist in `tui/internal/ui/preflight/lemonade.go:222-361`. Shipping
it as a component would cost a separate build, its own signing, and a version handshake, to
avoid writing less code than the handshake would take.

### 2.2 Connections stay with the agent

Signing in to Google is owned by the **agent**, not by `gaia`. This is how it works today —
`hub/agents/email/python/packaging/freeze.py:112-124` bundles `gaia.connectors` into the email
binary, so the agent runs its own OAuth flow and stores its own token.

Consequences, accepted deliberately:

- The Go port needs **no** OAuth code, no keyring integration, and no credential migration for
  existing users. This is the single largest reason the port is ~3,000 lines and not ~4,500.
- There is **no unified Connections view**. At N agents that is N browser flows and N stored
  credentials with no one place to see or revoke them.
- It assumes every future agent bundles the connector stack. **An agent that does not is a
  design problem to catch at publish time, not at run time.**

If the product later wants one Connections panel spanning agents, budget ~1,500 additional
lines plus a credential migration whose feasibility on Windows is unverified (§9.4).

### 2.3 The resolution rule

**The Go binary never resolves anything by `PATH`.** It resolves an absolute path from an
explicit order, or it reports that the thing is missing. No `exec.LookPath`, no scanning for
a nearby virtual environment, no "whichever one we find first."

This is the general form of the bug at `tui/internal/daemon/client.go:247`, which starts the
background service by asking the OS to find a program called `gaia` — after the rename, it
finds itself. Fixing that one line is not the fix. Never asking the question is the fix, and
it is the reason two programs came to share one name in the first place.

---

## 3. Distribution

### 3.1 One artifact, four wrappers

Four channels, chosen per platform and per user. They are **thin wrappers over one signed
release artifact per platform**, not four builds:

| Channel | Audience | What it does |
|---|---|---|
| `curl \| sh` / `irm \| iex` | anyone on a terminal | download, verify, install to `~/.gaia/bin/` |
| Homebrew / winget / apt | package-manager users | same artifact via the manifest |
| DMG / MSI / deb | double-click users | same artifact, bundled |
| — | developers | `pip install amd-gaia` ships **no** `gaia`; see §5 |

Every channel's entire job is to place `gaia` on `PATH`. Everything after that is identical,
which is what keeps four channels from becoming four behaviours.

### 3.2 The hub is the artifact store — but not the catalog

`gaia` ships as a **component in the hub's artifact store**, using the same manifest shape as
an agent: platform key → `{url, sha256}`, verified download, `.installed` sentinel carrying
the hash. This is `src/gaia/daemon/sidecars/fetch.py`'s contract, and reusing it means the
binary, the agents, and any future component share one integrity story.

**It does not appear in `index.json`** — the browsable agent catalog the TUI renders. `gaia`
is not an agent; listing it there would have the TUI offering to install itself.

Because the hub is plain JSON over HTTPS, there is **no bootstrap circularity**: a 20-line
shell script can read the manifest with `curl` and verify with `sha256sum`. The Go client is
not required for the first hop.

### 3.3 Two mirrors, one hash

The hub is primary; GitHub Releases is a mirror. The manifest lists both URLs per platform
under **one** `sha256`:

```json
"darwin-arm64": {
  "sha256": "a1b2c3…",
  "urls": [
    "https://hub.amd-gaia.ai/bin/gaia/0.x/darwin-arm64",
    "https://github.com/amd/gaia/releases/download/v0.x/gaia-darwin-arm64"
  ]
}
```

The client tries them in order and verifies against the same hash either way. **A hash
mismatch is a hard error, and exhausting both mirrors is a hard error** — there is no "use it
anyway" path. This is not a silent fallback: it is one artifact available from two locations,
with identical verification.

Rationale: without a mirror, a hub outage blocks installing GAIA itself, not merely installing
an agent. `publish.yml` already creates GitHub Releases with `release-assets/*`, so the mirror
is nearly free.

### 3.4 Where the publish lives

`package-publishing.mdx` already locks a registry-driven publisher — `hub/packages.yaml` +
`release.yml` + `publish_packages.yml` — with a `binary` channel that POSTs to the Agent Hub
Worker's `/publish`. **`gaia` becomes one registry block there, not another bespoke workflow.**
That registry's `freeze:` config assumes PyInstaller and needs a Go build variant; that is the
only new machinery.

`build_tui.yml` already cross-compiles six targets and uploads them as 14-day CI artifacts
(`.github/workflows/build_tui.yml:96-101`). Publishing is a change of destination, not a new
build.

### 3.5 Signing

Unresolved and out of this document's control, but it must be stated rather than discovered:

- **The Go binary is not signed today.** `.signpath/policies/gaia.policy` is scoped to
  `build-installers.yml` with `artifact_types: [nsis-installer]`; a bare executable would be
  **rejected server-side**. It needs its own policy or an extended one.
- The certificate is SignPath's OSS tier, so SmartScreen reputation accrues only after a few
  thousand downloads. Early Windows users see a warning regardless of what is built.
- macOS notarization is specced in `email-agent-packaging.mdx` Phase 4 with cert ownership
  listed as an open decision. That decision blocks this too.

---

## 4. First run and the readiness check

### 4.1 First run is not a new screen

The readiness gate in `tui/internal/ui/preflight/` already walks a user from a broken machine
to a working one, one keypress per fix. **First-run setup is one more row at the top of that
ladder**, not a separate flow:

```
  Getting Email ready                                    1 of 5 ready
  ─────────────────────────────────────────────────────────────────────
    [ok]  Background service     running
  > [!]   Local AI               not running
          GAIA needs a local model server. It runs on your machine;
          no email text ever leaves it.
          f  start it for me
    [  ]  AI model               —  (checked once Local AI is up)
    [  ]  Mailbox                —
  ─────────────────────────────────────────────────────────────────────
  f  fix this  ·  r  re-check  ·  d  details  ·  esc  back
```

Checks run in dependency order and stop at the first failure, because "model not downloaded"
is meaningless advice when the server is down. That ordering already exists and
`/v1/email/init` already emits hints in that order.

### 4.2 On demand: one check, two depths

The readiness check must be reachable at any time, not only on the launch path.

- **Launch depth** — fast, scoped to the agent being started, stops at the first failure.
- **Full depth** — every installed agent, free disk, model-file integrity, and component
  version agreement between `gaia` and its pieces.

**Same rows, same wording, same fix keys, same renderer — one code path with a depth
parameter.** Two screens would drift and eventually disagree about whether the machine is
healthy, which is worse than having only one.

Three entry points, because there are three situations:

| Entry | Situation |
|---|---|
| `c` on the hub screen | in the TUI. Free in the current keymap (`docs/plans/tui-user-journey.md` §4 binds `↑↓jk enter / i d s b r v q ?`) |
| `gaia doctor` | over SSH, in a script, or pasting into a bug report |
| `/doctor` in a session | mid-conversation; a slash command, so rule R4 holds |

### 4.3 Diagnose and repair are separate, always

**A check never changes the machine.** It reads state and reports. Every repair sits behind an
explicit keypress, and every repair states what it will do and how large it is *before* it
runs. A "validate my setup" run that silently begins a 4 GB model download is the exact
surprise that costs a tool its user's trust.

Note that `tui/internal/ui/preflight/provision.go` already crosses this line — it pulls a
model. That is correct behaviour for a fix key and incorrect for a check; the depth parameter
must not carry repair with it.

### 4.4 On a machine with nothing

The full check on a fresh machine has exactly one actionable row — install what is missing —
and every row below reads `— (checked once the above is fixed)`. This is honest, and it is why
first-run needs no separate design.

---

## 5. What happens to the Python CLI

### 5.1 It becomes `gaia-dev`, and it is developer-only

The Python CLI is renamed `gaia-dev` and ships only to people working from a checkout. The
existing `gaia-cli` alias (`setup.py:335`) becomes a deprecated pointer at it.

`gaia-cli` is rejected as the permanent name: it reads as "the command line for gaia," which
is precisely the confusion being eliminated. `gaia-dev` says what it is.

### 5.2 `pip install amd-gaia` becomes the agent-building kit

Keep the name — it is established and depended upon — and change what it *is*. When the
management layer moves to Go and the per-agent commands are removed, what remains in the wheel
is a library and a dev tool:

- the SDK an agent is written against — base `Agent`, tool mixins, LLM clients, RAG
- `gaia-dev` — evaluate, test, author, publish

Its second audience is unchanged: developers embedding a GAIA agent in their own Python
application.

**It ships no `gaia` console script at all.** Dropping `gaia = gaia.cli:main` from
`setup.py:333` means at most one program named `gaia` can ever exist on a machine, which
closes the collision class rather than managing it.

### 5.3 The 36 commands, resolved

Most of the surface is not legacy to be deleted. It is developer tooling that was never meant
to be on a user's machine, and it stops shipping to users automatically once users receive a
compiled program instead of a Python package:

| Group | Disposition |
|---|---|
| Agent-as-command (`email`, `jira`, `docker`, `sd`, `blender`, `summarize`, `browse`, `analyze`) | **Removed.** Each already exists as a hub agent; `gaia run <id>` covers it |
| Developer tooling (`eval`, `report`, `perf-vis`, `test`, `agent`, `youtube`, `stats`) | **Stays in `gaia-dev`.** Never shipped to users |
| Management (`hub`, `daemon`, `install`, `uninstall`, `init`, `config`) | **Ported to Go** per §2, and **removed from `gaia-dev`** — §2.1 forbids both |
| `connectors` | **Stays with the agent** per §2.2. Not in `gaia`, not a user command |
| Infrastructure (`api`, `mcp`, `telegram`, `schedule`, `knowledge`, `memory`, `cache`, `diagnostics`) | **Case by case** — see §9.3; several are developer or integration surfaces, not user surfaces |

Applying §2.1 to the middle row is the part that is easy to get wrong: porting `gaia init` to
Go while leaving `gaia-dev init` in place is not a separation, it is a copy — and the two will
disagree within a release.

The precise per-command verdicts, with test-coverage and doc-reference counts, live in the
triage report referenced in §9.3. **The capability-loss ledger in that report is a required
read before executing plan 3** — several hub agents have close to zero behavioural test
coverage (`docs/plans/port-audit-6-agents.md:34-36`), so "the hub agent covers it" is a claim
to verify per agent, not to assume.

---

## 6. The rename

`gaia` is the Go binary; the Python CLI is `gaia-dev`. This is settled, and it happens **in
Phase 1**, not at the end.

**Renaming is safe early; removing is not.** The forwarding shim (§6.2) means no capability is
lost when the name moves — every old command still reaches its implementation, with a warning.
What must wait for Phase 4 is *deletion* of the agent-as-command surface, because that is a
genuine capability change and it strands users if it precedes an installable replacement.

Two options were weighed and rejected. Shipping the Go binary as `gaia-tui` and leaving `gaia`
on the Python CLI avoids all churn, but leaves the name a user naturally types pointing at 36
developer commands — the exact confusion this design exists to remove, preserved by
construction. Shipping as `gaia` on user channels only, with developer checkouts unchanged,
defers the churn but creates a window in which `gaia` means different things on different
machines. The full rename with a forwarding shim costs more up front and leaves no ambiguity
behind.

What follows is the blast radius.

### 6.1 Two collisions that are dangerous, not merely untidy

| Command | Python meaning | Go meaning | Risk |
|---|---|---|---|
| `gaia uninstall` | tiered purge of the **entire** GAIA install (`src/gaia/installer/uninstall_command.py`) | remove **one agent** (`tui/internal/cli/agents.go:263`) | same word, wildly different blast radius |
| `gaia install --lemonade` | install Lemonade Server | install **an agent** (`tui/internal/cli/agents.go:141`) | different noun entirely |

Muscle memory and roughly 1,800 existing doc references point the wrong way after the rename.
Neither may be left to fail by accident.

### 6.2 The forwarding shim is required, not optional

`gaia <unknown-command>` must not dead-end. When `gaia` receives a command it does not
implement:

- **On a developer machine** (`gaia-dev` present): forward to it, verbatim, with a one-line
  deprecation notice on stderr.
- **On a user machine** (no `gaia-dev`): a loud, specific error naming what replaced it —
  `gaia init` → "setup is now built in; press `c` or run `gaia doctor`".

Without this, day one of the rename breaks `gaia init` — the first command in
`docs/quickstart.mdx` — for every existing user. With it, ~1,800 documentation references and
~650 runtime remedy strings degrade to a warning and can be swept over weeks instead of in one
commit.

### 6.3 Same commit as the rename

Anything in the **silent-wrong-behaviour** class, by definition:

1. `tui/internal/daemon/client.go:247` — `exec.LookPath("gaia")`, which after the rename
   resolves to the Go binary itself. Its `pip install -e .` remedy text goes at the same time.
2. `src/gaia/apps/webui/services/backend-installer.cjs` — `findGaiaBin()` resolves the venv's
   `gaia`; a Go `gaia` earlier on `PATH` makes it resolve the wrong program **without
   erroring**.
3. `setup.py:333` — drop `gaia = gaia.cli:main`.
4. The forwarding shim (§6.2).
5. `tests/unit/test_remedy_commands_are_runnable.py` — this is a **guardrail inversion**, not
   a routine test update. Left unchanged it keeps passing precisely while the remedies are
   wrong, certifying the bug. CLAUDE.md's "a shipped remedy must actually parse" rule depends
   on this file being correct.

Everything else — the doc sweep, the website, the ~650 runtime remedy strings, the
`gaia tui …` prefix deprecation — follows, because it fails loudly.

### 6.4 Verify before writing the plan

Two claims in the audit are structural reads of cobra, not executed. Both are 30-second checks
and one is the most consequential row in the table:

```bash
cd tui && go run ./cmd/gaia hub install email > /tmp/o.txt 2>&1; echo "exit=$?"; cat /tmp/o.txt
cd tui && go run ./cmd/gaia --version              > /tmp/v.txt 2>&1; echo "exit=$?"; cat /tmp/v.txt
```

`root.go` never sets `rootCmd.Version`, so cobra skips registering `--version` entirely. Also
unaudited: `.github/workflows/` for `gaia` invocations.

---

## 7. Sequencing

Four phases. Each is independently shippable and independently useful, and the order exists to
guarantee **nothing is removed before its replacement is installable**.

### Phase 1 — Publish the binary, and take the name

Wire `gaia` into `hub/packages.yaml`, publish to the hub artifact store with the GitHub
Releases mirror, and stand up the `curl | sh` script at the URL the website already advertises.

**The rename lands here**, with the five same-commit items in §6.3 — most importantly the
forwarding shim, without which day one breaks `gaia init` for every existing user. Nothing is
removed in this phase; every old command still works, with a deprecation notice.

Immediately useful to anyone who already has GAIA installed — they have the Python background
service, so the binary works against it today. Unblocks everything else.

**Done when:** a person who already runs GAIA can install `gaia` in one command and use it to
browse, install and run agents — and every command they knew before still works or tells them
exactly what replaced it.

### Phase 2 — Move the management layer into Go

The port: agent install/uninstall, daemon lifecycle, Lemonade provisioning, and agent
supervision. **~2,600–3,350 lines of new Go, ~6,000–9,000 with tests** (§9.4). Sign-in is not in
it — §2.2 leaves that with the agent, which is the largest single reason the number is this
small. The scheduler, model-slot broker, connection forwarding and custody migrations are out
of scope, and `relay.py` disappears entirely rather than porting.

Internal order, cheapest and safest first:

1. **Catalog + install/uninstall** — low risk, and leaves the product strictly better even if
   work stops here
2. **Daemon lifecycle** — instance/lock/pid protocol; `tui/internal/daemon/` already has the
   client half
3. **Lemonade provisioning** — independent of 1 and 2, parallelisable with a different owner
4. **Agent supervision** — the hard one. Spawn, the secret file, the Windows DACL, health and
   version gating, tree-kill, orphan reaping. **Do it last, and do it on real Windows
   hardware.** Prerequisite: the launch contract is written down (§9.4 risk 2)

**Done when:** a machine with `gaia` and no Python can install an agent, get Lemonade and a
model, and run the agent.

### Phase 3 — Open the user channels

Homebrew, winget, apt, and the desktop installers, plus the readiness ladder's stage-0 row and
the on-demand `doctor`.

**Done when:** a blank machine reaches a working agent without a doc, without a second command,
and without an unrecoverable wall.

### Phase 4 — Retire

The agent-as-command surface (`gaia email`, `gaia jira`, …) is removed, the deprecation
notices come off, and the doc sweep finishes. **Last, deliberately.** The name moved in Phase 1
because a shim made it lossless; *removing* capability is what strands users, and it must not
precede an installable replacement on a blank machine.

Gated on the capability-loss ledger in the triage report (§9.3): a hub agent with near-zero
behavioural test coverage is not yet a replacement for the command it supersedes.

---

## 8. Explicitly out of scope

| Not here | Where it belongs |
|---|---|
| The developer / agent-authoring flow | `docs/plans/agent-factory.md` |
| **Who may publish to the hub, and what review they get** | Needs its own spec — see §9.2 |
| Thin agents sharing a runtime | Rejected — agents are compiled binaries; see §9.5 |
| Conversational OAuth | #2469 |
| Journey stages 1–11 | `docs/plans/tui-user-journey.md` |

---

## 9. Evidence, risks and open questions

### 9.1 The measured freeze (now historical, kept for the numbers)

A frozen Python engine covering the daemon, `init`, the hub installer and connectors was built
and measured at **73 MB** on darwin-arm64, with **zero fatal module-level ML imports** across
all four surfaces — the ML stack genuinely is reachable only lazily. The design rejects the
engine for architectural reasons, not feasibility ones. Three findings from that work survive
the rejection and apply to **agent** freezes, which continue:

1. **Freeze size is silently build-environment-dependent.** `src/gaia/rag/sdk.py:41` imports
   faiss at module level, reachable via a lazy chain. The 73 MB used the `[api]` extra; from
   `[ui]`, PyInstaller silently collects faiss, pymupdf, python-docx, python-pptx and
   sentence-transformers — tens of MB, **no warning, no build failure**. The freeze environment
   must be pinned and CI must assert a size ceiling.
2. **Six platforms is not real.** `linux-arm64` and `win32-arm64` have zero precedent in this
   repo. The honest posture is 3 required + 1 best-effort, which is the shape email already
   ships.
3. **A wrong remedy already exists.** `src/gaia/hub/installer.py:401-441` and
   `src/gaia/installer/init_command.py:435-470` lose two of three pip frontends under freeze
   and print an impossible fix (measured, exit 2). Exactly CLAUDE.md's "a command that exists
   but means something else" class.

### 9.2 The trust question this design does not answer

`src/gaia/hub/catalog.py:354` has `_requires_trust(security_tier)`. The field exists; the
policy behind it does not. At the stated target of hundreds of agents, **who may publish and
what review they receive is a harder problem than any packaging in this document.** It needs
its own spec before third-party agents land, and this design does not pretend to cover it.

### 9.3 Research inputs

Three read-only reports back this document and should be read before the corresponding plan:

| Report | Feeds |
|---|---|
| CLI triage — per-command verdicts, test coverage, doc references, **capability-loss ledger** | Phase 4 (§5.3) |
| Rename blast radius — prioritised site table, silent-vs-loud classification | Phase 4 (§6) |
| Engine freeze feasibility — measured sizes, import graph, per-platform build cost, signing | §9.1, agent freezes |

### 9.4 The size of the Go port — measured

**~2,600–3,350 lines of new Go**, or ~3,000–4,500 including error taxonomy, wiring and the CLI
surface. At this repo's Go test ratio (17,447 source vs 16,502 test across `tui/`), budget
**~6,000–9,000 lines total.**

One subsystem disappears rather than porting: **`src/gaia/daemon/relay.py` (450 lines) is not
needed at all.** It exists so that client credentials never reach a sidecar (`relay.py:1-41`).
When the Go binary *is* the supervisor it already holds the bearer token, so there is nothing
to proxy. Likewise the scheduler, the model-slot broker, connection forwarding and the custody
migrations serve features outside the user flow and are not in scope.

Ordering within the port, cheapest and safest first: catalog and install, then daemon
lifecycle, then Lemonade provisioning (independent, parallelisable), and **agent supervision
last**, on real Windows hardware.

**Top three risks:**

1. **Windows, three risks stacked.** Replicating the Windows DACL has two silent-fail-open
   details (`PROTECTED_DACL`, verify-after-write). Detached spawn plus `taskkill /T /F`
   tree-kill has no POSIX analogue to lean on. And `src/gaia/daemon/sidecars/ledger.py:233-245`
   documents that orphan reaping on Windows is **already an unsolved gap in Python** — the port
   inherits an open problem, not a solved one. Nobody on this repo appears to develop on
   Windows, so each of these fails in the field rather than in CI.
2. **The sidecar launch contract is undocumented and already drifting.**
   `src/gaia/daemon/sidecars/manager.py` is the only specification, and
   `hub/agents/email/npm/SPEC.md:59-61` is a second implementation that has already diverged —
   it uses the environment channel, not the 0600 file. Go would be the third. The failure mode
   is not a crash: it is a Go supervisor quietly taking the deprecated bare-env leg, putting the
   launch secret in `/proc/<pid>/environ` on every Linux box, with nothing failing anywhere.
   **Write the contract down before porting it**, and make the version gate at
   `src/gaia/daemon/sidecars/spec.py:99` enforceable from both sides.
3. **The delegation assumption is load-bearing and proven for exactly one agent.** §2.2's
   estimate holds only because the email binary bundles the connector stack. An agent that
   ships without it re-opens ~1,500 lines and the Windows credential migration.

**Unverified, and worth a 30-minute empirical check before it drives anything:** whether Go and
Python keyring libraries are wire-compatible on Windows (`TargetName` / UTF-16 handling). It
does not block the current design — §2.2 means Go touches no credentials — but it is the gate
on ever taking Connections into the host.

### 9.5 Rejected: thin agents on a shared runtime

Considered and dropped. Fat frozen agents duplicate a runtime N times (~90 MB each), which at
hundreds of agents is real cost, and the alternative was a shared interpreter in a portable
engine. **It was rejected because it reintroduces Python onto the user's machine** — the exact
thing this design removes — to solve a disk-space problem with better levers. The lever is
trimming what each agent's freeze includes: the email binary carries faiss, numpy and a slice
of GAIA core it does not need at runtime. That is a per-agent build-config fix, not an
architecture change.

Note that a `kind` discriminator on the artifact manifest is still worth carrying, because
agents will not all be Python forever.

### 9.6 Known-broken today, fix regardless of this design

- `https://amd-gaia.ai/install.sh` and `install.ps1` are advertised in
  `website/src/pages/index.astro:76` and `docs/quickstart.mdx:111,133`, and **404**. Nothing in
  `deploy_website.yml` copies `installer/scripts/install.sh` into `website/public/`.
- `gaia --version` does not work — `tui/internal/cli/root.go` never sets `rootCmd.Version`, so
  cobra skips registering the flag (structural read; verify per §6.4).
- `binaries.lock.json` ships `"size": 0` for all four platforms — never populated.

---

*This document is `.md` under `docs/superpowers/specs/` and is deliberately not registered in
`docs/docs.json`, matching `tui-user-journey.md`.*
