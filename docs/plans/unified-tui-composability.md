# Converging on one TUI a non-developer can compose GAIA with

Planning document for the "single TUI, on the hub front page, manages agents + skills +
components" goal. Assessed against `feat/gaia-flagship-agent-2804` at `418a03b2`
(2026-08-12), while the minimal `gaia` agent + skills release is still in flight.

Companion to [`tui-user-journey.md`](tui-user-journey.md) (the UX design for the *chat*
journey, much of which has since landed) and [`agent-hub-ui.mdx`](agent-hub-ui.mdx).
Written in the shape of [`gaia-agent-readiness.md`](gaia-agent-readiness.md).

**Verdict: go, but the work is not in the TUI.** The terminal hub is further along than
the vision assumes — it already browses, installs, trust-gates, preflights, and chats —
and "single source" is one sibling PR from done. What blocks composability is one layer
down: the daemon's catalog only offers agents whose id appears in a hardcoded two-entry
Python table, and there is no daemon route for skills at all. Until those two things
change, no TUI screen can show a third agent or a single skill, however well designed.

---

## 1. Current state

Every row checked against source on this branch.

| Capability | Status | Evidence |
|---|---|---|
| One TUI binary, cross-compiled 6 targets | **Done** | `tui/cmd/gaia/main.go`; `build_tui.yml` matrix |
| Published as the `terminal-hub` component | **Done** | `.github/workflows/release_components.yml:119-220`, manifest `hub/components/terminal-hub/gaia-agent.yaml` |
| *Only* published there (no duplicate lane) | **Done** | `release_agent_gaia.yml` no longer builds or publishes a TUI: its `tui` job downloads the 6 `terminal-hub` artifacts, cross-checks each against the hub's own recorded SHA-256, and records them in `binaries.lock.json` (schema 3.0, per-component `baseUrl`). A guard in the publish job rejects any non-`gaia-agent-*` binary staged for upload, and `publish_to_r2.py` no longer recognises a `tui` component. |
| Browse the hub catalog in the TUI | **Done** | `tui/internal/catalog/hub.go`, `tui/internal/ui/hub/model.go:38-57` |
| Install / uninstall an agent from the TUI | **Done** | `tui/internal/ui/hub/install.go`; `POST /daemon/v1/agents/{id}/install` |
| Consent gate before running third-party code | **Done, and good** | `tui/internal/ui/hub/trust.go` — names publisher, version, tier, size, permissions; defaults to *not* approved |
| Readiness gate with one-keypress fixes | **Done** | `tui/internal/ui/preflight/model.go:66-84`, `check.go` |
| Chat with an installed agent | **Done** | `tui/internal/ui/chat/model.go`; SSE relay `POST /v1/{id}/query` |
| Mid-turn questions + destructive-action confirm | **Done** | `tui/internal/ui/components/question.go`, `confirmation.go` |
| Loopback control API (drive the TUI in tests) | **Done** | `tui/internal/control/server.go`, `/control/v1/{status,screen,keys,text,wait,frames,resize}` |
| Daemon offers **any** agent beyond email + gaia | **Blocked** | `src/gaia/daemon/sidecars/install.py:150-151` filters every catalog id not in `builtin_specs()`; that table has exactly two entries (`spec.py:314`, `:331`) |
| Skill lane reachable from the daemon | **Unstarted** | Grepped `src/gaia/daemon/sidecars/routes.py` — no skill route of any kind |
| Skill publish → hub | **Unstarted** | Worker endpoint exists (`workers/agent-hub/src/skill-publish.ts`); **no** GitHub workflow triggers it. Ten skills sit in `hub/skills/`, zero are published. |
| TUI screen for skills | **Absent** | Grepped `tui/internal/` — no skill model, view, or client call |
| TUI screen for components | **Absent** | The hub list is agent-shaped (`catalog.Agent`); components are not a browsable lane |
| TUI toggle for memory | **Absent** | Grepped `tui/` — nothing. Memory is agent-internal. |
| Connector / OAuth management in the TUI | **Partial (read-only)** | Preflight *detects* a missing connector and prints the `gaia connectors connect …` remedy (`preflight/check.go:784-820`), then the user leaves the TUI to run it |
| Skills enabled in the flagship agents | **Off by policy** | Both `hub/agents/gaia/python/gaia-agent.yaml:51-73` and `hub/agents/email/python/gaia-agent.yaml:56-70` comment out `default_skill_set` pending an eval gate (#2848 / #2695) |
| Hub "front page" to be featured on | **Does not exist as a page** | `workers/agent-hub/` serves `index.json` only; no HTML, and no featured/pinned/curated field in `schemas/index.schema.json`. Ordering is `sort by id` (`catalog.ts:379-429`). |

The live catalog (`hub.amd-gaia.ai/index.json`) was generated 2026-07-16 and lists exactly
one entry: `email` 0.5.0. No `terminal-hub`, no `gaia`, no skills.

---

## 2. What already exists that this builds on

The pleasant surprise: the two hardest parts of a composability UI are already written and
shipped, just pointed at agents instead of at skills and components.

**The trust gate is the skill-consent screen, already built.** `tui/internal/ui/hub/trust.go`
exists because the daemon returns 403 for an unverified agent, and its own comment says a
plain yes/no box "gives the user nothing to decide with" — so it renders publisher,
version, security tier, download size, and the requested permissions, with the safe option
focused by default. That is precisely the decision a non-developer must make before
installing a skill. It needs a second data source, not a redesign.

**The preflight gate is the composability screen, already built.** `preflight/model.go`
already models "a list of preconditions, each with a status, each with a one-keypress fix,
launch is gated until green." Connectors, model, daemon. "Enable the email agent" and
"turn on memory" are the same shape: a row, a state, a fix.

**The Python skills runtime is close to complete.** Not a stub — install resolves a
version range, downloads, verifies the bundle signature (`install.py:242-247`), computes
the effective tier as `min(claimed, attested)` (`tiers.py:104-113`), enforces a per-tier
permission ceiling (`enforce_tier_ceiling`), and refuses local-capability permissions at
*both* install (`install.py:353`) and load (`loader.py:62`). Eleven `gaia skill`
subcommands exist including `search`, `install`, `remove`, `publish`, `trust`, and `audit`.

**The catalog already models skills as a first-class lane.** `SKILL_PACKAGE_TYPE = "skill"`,
`is_skill_entry()`, `skill_entries()` (`src/gaia/hub/catalog.py:358-382`), and the worker
puts skills in the same `agents[]` array discriminated by `type` (`catalog.ts:379-429`).

**The control API makes all of this testable without a human.** `/control/v1/keys`,
`/screen`, and `/wait` mean every screen sketched in section 3 can have an end-to-end test
that presses the keys and asserts on the rendered output. Use it (see the
`driving-the-tui` skill); do not sleep-and-screenshot.

---

## 3. The non-developer UX

The audience is someone who has never opened a terminal on purpose. Three rules fall out
of that, and they should be treated as acceptance criteria, not taste:

1. **No screen may end in "now run this command."** Today preflight prints
   `gaia connectors connect google --scopes … --grant-agent installed:email` and the user
   is expected to leave, paste it, and come back. That is the single largest non-developer
   failure in the current product.
2. **Every install decision names what it can do to you, in words.** The trust gate already
   does this. Skills must not get a weaker gate than agents.
3. **80×24 must render.** `tui-user-journey.md` flagged the home screen budgeting ~31 rows
   (`hub/model.go:357-362`). Any new screen inherits that bug if it is not designed against
   24 rows.

### 3.1 One home screen, three lanes

The hub list is tabbed `Installed | Available | Coming Soon` today
(`catalog.go:26-29`) — a taxonomy of *install state*, which is not what the user is
choosing between. Composability needs a taxonomy of *kind*.

```
┌ GAIA ────────────────────────────────────── ● connected · local ┐
│                                                                  │
│   Agents      Skills      Add-ons      Settings                  │
│  ─────────                                                       │
│                                                                  │
│   ● GAIA               ready          chat, documents, research  │
│   ○ Email Triage       needs setup    Gmail / Outlook            │
│   ↓ Analyst            not installed  spreadsheets, charts       │
│                                                                  │
│                                                                  │
│  enter open · i install · x remove · tab switch · ? help         │
└──────────────────────────────────────────────────────────────────┘
```

`●` ready · `○` installed, needs setup · `↓` available. Status is a glyph *and* a word —
`statusbar.go:24-25` currently distinguishes connected from disconnected by colour alone,
which fails for colour-blind users and for piped output.

"Add-ons" rather than "Components": the user is not shopping for software components, they
are adding the Agent UI or a voice model. Naming is an open question (§6).

### 3.2 Enable the email agent — the flagship flow

The user presses `enter` on *Email Triage*. Everything that is currently a printed remedy
becomes a row they can act on.

```
┌ Email Triage — setup ────────────────────────────────────────────┐
│                                                                  │
│   ✓  Installed                                v0.5.0             │
│   ✓  Local AI model ready                     Gemma-4-E4B        │
│                                                                  │
│ ▸ ✗  Mailbox not connected                                       │
│      Email Triage reads and labels your mail. It needs           │
│      permission from Google or Microsoft. Your password is       │
│      never seen by GAIA — you sign in on their page.             │
│                                                                  │
│         [ Connect Google ]   [ Connect Outlook ]                 │
│                                                                  │
│   ·  Memory                                   off                │
│      Remembers who matters to you, so triage improves.           │
│                                        [ turn on ]               │
│                                                                  │
│  enter run the highlighted fix · esc back                        │
└──────────────────────────────────────────────────────────────────┘
```

Pressing `[ Connect Google ]` opens the system browser, the user signs in, the row flips to
`✓ Connected as name@gmail.com` without them touching the terminal. Mechanically this is
`gaia connectors connect google --grant-agent installed:email --scopes …`
(`src/gaia/connectors/cli.py:151-227`) driven over a daemon route instead of a shell. Two
facts make this feasible and should be stated plainly: GAIA ships its own public PKCE
client, so **the user does not register an OAuth app**; and there is already a device-code
path (`--device`) for the headless/SSH case where no browser exists.

When it completes, the screen goes green and drops into chat. No command was typed.

### 3.3 Browse and install a skill

```
┌ Skills ──────────────────────────────────────────────────────────┐
│                                                                  │
│   Search: rss▏                                                   │
│                                                                  │
│   ↓ RSS Digest         experimental   summarise your feeds       │
│     Daily Brief        experimental   morning summary            │
│     Price Watch        experimental   track a price, tell you    │
│                                                                  │
│   ┌ RSS Digest ────────────────────────────────────────────┐     │
│   │ Turns a list of feeds into a short daily digest.       │     │
│   │ Works with: GAIA                                       │     │
│   │ Needs: read from the internet                          │     │
│   │ Ships code: yes — runs inside GAIA                     │     │
│   └────────────────────────────────────────────────────────┘     │
│                                                                  │
│  i install · enter details · esc back                            │
└──────────────────────────────────────────────────────────────────┘
```

`i` must not install. It must open the consent gate — the trust-gate shape reused verbatim:

```
┌ Install RSS Digest? ─────────────────────────────────────────────┐
│                                                                  │
│  This skill is unverified. Nobody GAIA trusts has signed or      │
│  reviewed it.                                                    │
│                                                                  │
│  It ships code that runs inside GAIA, with the same access       │
│  GAIA has — your files, your connected accounts, the internet.   │
│                                                                  │
│    From      github.com/amd/gaia · starter pack                  │
│    Can do    read from the internet                              │
│    Code      tools.py — 1 tool (fetch_feed)                      │
│                                                                  │
│           [ Cancel ]        [ Install anyway ]                   │
│            ^^^^^^^^                                              │
│  esc cancel · ← → choose · enter confirm                         │
└──────────────────────────────────────────────────────────────────┘
```

The wording is not decoration. It is the plain-language rendering of a real refusal that
already exists: `install.py:364-372` composes almost exactly this sentence — *"that Python
is imported and run in your agent's own process, with your agent's access, the first time
the skill loads"* — and raises unless `--allow-experimental` was passed. The TUI must
surface that error as a screen, never suppress it by passing the flag. See §4.1: today
**every** skill hits this path, which is a problem in itself.

### 3.4 Memory and add-ons

Memory is the simplest possible row, and it must be honest about the dependency:

```
   Memory                                          ● on
   GAIA remembers facts you tell it, across sessions.
   Stored on this computer only (~/.gaia/memory.db).
                                        [ turn off ]  [ forget everything ]
```

When Lemonade is down, memory does not fail — it *silently* disables itself
(`src/gaia/agents/base/memory.py:506-567` sets `_memory_store = None`, logs a warning, and
returns). A user who typed a fact and finds it forgotten has no way to learn why. The row
must read the real state, not the setting:

```
   Memory                                     ⚠ paused
   The local AI service isn't running, so nothing is being
   remembered right now.                          [ start it ]
```

There is no toggle to bind to. `GAIA_MEMORY_DISABLED=1` is an env var read at init
(`memory.py:432-452`), and `~/.gaia/memory_settings.json` holds an unrelated
`system_context_enabled`. A persisted, daemon-readable memory preference is net-new.

---

## 4. Hard constraints, currently unenforced

### 4.1 One-click skill install — the load-bearing safety question

**Python does not refuse `tools.py`. It executes it.** `loader.py:158-168` builds a module
spec from the skill's `tools.py` and calls `spec.loader.exec_module(module)` — arbitrary
Python, in the agent process, with the agent's file access, connector tokens, and network.
This is by design; `hub/skills/rss-digest/tools.py` ships one, and the skill format's
progressive disclosure depends on it. It is the correct Python behaviour and the exact
opposite of the C++ position in [`gaia-agent-readiness.md`](gaia-agent-readiness.md) §4,
where the same file must be refused because no such channel exists.

That divergence is fine as long as the human sees the difference. Three findings say the
current gate will not survive contact with a one-click UI:

1. **`tools.py` bypasses the permission model entirely.** `refuse_unbridged_permissions()`
   (`permissions.py:139`) refuses `shell:execute` and every other local-capability domain,
   at install and at load. But a skill that *declares nothing* and ships a `tools.py` that
   imports `subprocess` gets everything, because permissions describe the bridge, not the
   code. A skill can be maximally dangerous while displaying `Needs: nothing`.
2. **Every skill is `experimental`, so the strongest warning becomes wallpaper.** There is
   deliberately no bundled AMD root key (`signing.py:25`, `:518`), so the trust store is
   empty on a fresh machine, nothing attests, and `effective_tier` floors everything at
   `experimental`. That tier is refused outright unless `--allow-experimental`. A TUI that
   shows a red unverified warning on 100% of skills has taught the user to dismiss it by
   the third one.
3. **The consent gate is a CLI flag and a `Confirmer` bound to `input()`**
   (`install.py:84`, `:144`). A TUI install path has to pass *something*. Passing
   `allow_experimental=True` and a `lambda _: True` silently deletes both gates — and it is
   the shortest path to a working demo, which is why it needs to be prohibited in writing
   now rather than caught in review later.

**Positions this plan takes:**

- The TUI never passes `allow_experimental` or an auto-approving `Confirmer`. It renders
  the refusal and requires a keypress on a non-default button. Assert this in a test that
  drives the control API and fails if install succeeds without the extra press.
- "Ships code" is shown as its own line on the card *and* the gate, derived from
  `skill.gaia.tools`, not from permissions.
- Get the AMD signing key into the picture before one-click install ships. Until at least
  the starter pack installs as `verified`, the tier signal carries no information — and
  publishing those ten skills is what the sibling release is doing anyway.
- The `scripts/` refusal that `gaia-agent-readiness.md` §4 flags as unowned is still
  unowned here. I grepped `hub/skills/` — no skill ships a `scripts/` directory today, so
  it is a latent hole, not an active one.

### 4.2 Prompt-token cost — the feature is currently switched off

Both agents that could load skills ship with them disabled: `gaia-agent.yaml:51-73` (gaia)
and `:56-70` (email), each pointing at #2848 / #2695 and each saying the same thing — the
prompt cost is real (~1,334 tokens for the email personal set, cutting the bulk-triage
result envelope from 6144 to 4810) and no eval evidence backs it.

A skills browser that installs skills nothing loads is worse than no browser. **The eval
gate is a hard dependency of the skills screen, not a follow-up.** Per CLAUDE.md, that
means an actual `gaia eval agent` run against the committed baseline before
`default_skill_set` is uncommented. Design consequence: the screen shows a running budget
("3 skills · about 4,000 words of instructions · leaves room for ~40 pages of documents")
and a per-agent cap that refuses the fourth skill rather than silently degrading answers.

### 4.3 Version skew across three moving parts

The TUI, the daemon, and each sidecar version independently and the user updates them at
different times. The existing handling is good and should be extended, not replaced:
`Instance.CheckVersion()` refuses to attach on a daemon MAJOR mismatch
(`tui/internal/daemon/instance.go`); the sidecar manager probes `/version` and raises
`VersionMismatchError` on MAJOR mismatch; and `negotiate.go:36-62` feature-gates optional
request fields by contract version (2.6 questions, 2.11 pre-scan, 2.12 session).

Every new surface in §3 adds a contract. Skill listing, skill install, connector-connect,
and memory-toggle each need a negotiated capability so an older TUI hides the tab instead
of 404-ing, and a newer TUI says "update GAIA to manage skills" instead of failing
mid-flow. Publishing the TUI on the hub front page makes this permanent: from that day, the
TUI's version is chosen by the user, never by the repo.

### 4.4 The daemon's two-entry table

`build_catalog()` computes `can_supervise = agent_id in known` and drops every other entry
(`install.py:150-151`). `known` comes from `builtin_specs()`, a static table containing
`email` and `gaia` (`spec.py:314`, `:331`). The docstring is explicit that this is
deliberate — refuse to install what the daemon cannot start — and the reasoning is sound.

But it means "the TUI interacts with **all** agents" is false by construction for the other
seventeen directories under `hub/agents/`, and the same id check will drop skill entries
and the `terminal-hub` component when they reach `index.json`, because they will never be
in a table of supervisable sidecars. There is also a hard `MAX_LIVE_SIDECARS = 3` cap with
no eviction (`registry.py`), which a UI inviting users to enable things will hit.

The fix is to make supervisability *data* — derived from the manifest the hub already
serves — and to route by `type` so skills and components take install paths that do not
require a sidecar spec at all. That is the single highest-leverage change in this document.

---

## 5. Critical path

```
  minimal gaia agent + skills published  (in flight, blocks everything)
        │
        ├──▶ C1 converge TUI to terminal-hub  (in flight, sibling task)
        │
        ├──▶ C2 catalog by type, not by table ──┬──▶ C4 skills screen ──▶ C6 memory + add-ons
        │         (unblocks agents+skills+       │      ▲
        │          components in one change)     │      │
        │                                        │   eval gate (#2848) — hard dep
        │                                        │
        └──▶ C3 skill publish workflow ──────────┘
                                                 │
             C5 in-TUI connector connect ────────┴──▶ C7 front page + featuring
```

**C1 — Drop the TUI from the gaia release lane.** Remove the `build_tui.yml` call and the
`gaia-tui-*` publish steps from `release_agent_gaia.yml`; `terminal-hub` in
`release_components.yml` becomes the only publisher. *In flight — confirm before starting
anything else, and confirm the npm launcher consumes `terminal-hub`.*

**C2 — Catalog and install route by `type`, not by a hardcoded id list.** Replace
`agent_id in known` with a manifest-derived check, and branch install on
agent / skill / component. Keeps the loud refusal for a genuinely unsupervisable agent;
stops it from being the reason no other lane exists. Also settle `max_live` — it is a
`SidecarRegistry` constructor default (`registry.py:40,47`), so raising it is a one-line
change nobody has had reason to make yet. **Everything else depends on this.** ~1 PR, mostly in
`src/gaia/daemon/sidecars/install.py` and `routes.py`, plus daemon tests.

**C3 — Skill publish workflow.** `release_skills.yml` calling the existing
`workers/agent-hub/src/skill-publish.ts`, gated on `skill_audit.yml` (whose contract
`tests/unit/test_skill_audit_workflow_contract.py` already pins). Publishes the ten skills
in `hub/skills/`. Independent of C2 — can run in parallel. ~1 PR.

**C4 — The skills screen.** New tab, `GET /daemon/v1/skills` + `POST …/skills/{name}/install`,
and the consent gate of §3.3 reusing `trust.go`'s shape. Never auto-approves. Control-API
test that install fails without the explicit second keypress. *Blocked by C2, C3, and the
#2848 eval gate.* ~2 PRs (daemon routes; TUI screen + gate).

**C5 — Connector connect without leaving the TUI.** Turn the preflight remedy string into
an action: daemon route that runs the PKCE flow, opens the browser, streams status back;
device-code fallback for SSH. This is the largest single UX win in the document and is
independent of the skills work. ~2 PRs.

**C6 — Memory and add-ons.** Persisted memory preference the daemon can read and the agent
honours, a row that distinguishes *off* from *paused* (§3.4), and the add-ons lane for
`agent-ui` / `terminal-hub`. *Blocked by C2.* ~1–2 PRs.

**C7 — Front page and featuring.** Needs a decision first: there is no HTML front page and
no featured field. Either the website (`workers/website-router/`) grows a page that reads
`index.json`, or `index.json` grows a `featured: []` and the site renders it. Either way,
add a schema field — alphabetical-by-id will not put the TUI first. ~1–2 PRs plus a
website change outside this repo's worker.

**Estimate: 9–11 PRs, roughly 3–4 agent-weeks**, after the minimal release lands. C2 and C5
are the load-bearing ones; C4 is gated on an eval nobody has run yet, so treat its date as
unknown rather than late. Parallelism ceiling is about three agents — C2, C3, and C5 are
mutually independent; everything downstream of C2 serialises behind it.

---

## 6. Open questions

Could not be resolved from source. Listed rather than guessed.

1. **Does "front page" mean the hub or the website?** `hub.amd-gaia.ai` serves JSON only;
   the marketing site is deployed separately and reverse-proxied. "Accessible on the front
   page" could mean a download button on https://amd-gaia.ai/, a featured card in a hub
   page that does not exist yet, or first position in `index.json`. Different work.
2. **Should the TUI install itself?** If a user's entry point is the TUI, they already have
   it. Featuring it on the front page is an *acquisition* problem (curl | sh, a release
   asset), not a catalog one, and no install script for `terminal-hub` was found.
3. **Does the npm `gaia` package become the distribution channel?** The sibling task wiring
   `hub/agents/gaia/npm` to consume `terminal-hub` may already answer this. If `npx gaia`
   is the front door, C7 is largely an npm/README problem.
4. **When does the AMD signing key exist?** §4.1 depends on it. `signing.py:25` says the
   absence is deliberate for now; nothing in the repo says what changes that.
5. **What is the memory preference's scope** — per agent, or machine-wide? Memory is a
   mixin on the agent (`memory.py`) but the store is a single `~/.gaia/memory.db`, so a
   per-agent toggle and a shared database will disagree about what "off" means.
6. **Naming.** "Components" is an internal word. "Add-ons" is used in §3 as a placeholder.
   Also unsettled from `tui-user-journey.md` decision D4: the Go binary's cobra root is
   `Use: "gaia"` while the wheel also installs `gaia`, so both on PATH is ambiguous.
7. **Is `MAX_LIVE_SIDECARS = 3` a real ceiling or a placeholder?** A UI that encourages
   enabling agents will hit it, and there is no eviction (the idle reaper is tracked as
   V2-15) — the fourth just fails with a capacity error.
8. **Skill ↔ agent compatibility.** A skill's card in §3.3 says "Works with: GAIA". Nothing
   in the skill front matter obviously declares which agents it targets; I did not find a
   compatibility field, but did not read `format.py` exhaustively.

---

## Recommendation

**Start C2 immediately after the minimal release, and do not start C4 until an eval backs
the skills prompt cost.** The TUI is not the problem — it already does the hard parts, and
its trust gate and preflight ladder are better foundations for composability than anything
that would be designed from scratch. The problem is that the daemon decides what the TUI is
allowed to show, and it decides from a two-entry Python table that predates skills and
components existing. Change that one function and three lanes light up at once; leave it and
every screen in section 3 is a mockup of data that cannot arrive.

The one thing to write down before any code: **the TUI must never pass
`--allow-experimental` or an auto-approving `Confirmer`.** Python executes a skill's
`tools.py` inside the agent, and the only thing standing between a non-developer and that
is a refusal the TUI would find it very convenient to suppress.
