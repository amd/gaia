# GAIA TUI — the user journey

Status: Design / not started
Owner: TBD
Companion to: [`email-agent-tui-port.md`](email-agent-tui-port.md) (engineering scope)
Related: #1186 (TUI), #2191 (email thin client), #2142 (daemon relay), #2469 (agent-led connector onboarding), #555 (autonomy epic)

---

## 0. The finding

**The port has one hard problem and it is not the transport — it is the four-precondition
gate between `gaia tui` and a triaged inbox.** Local LLM running, model downloaded, daemon
up, mailbox connected and granted. Today a user meets those one at a time, each as a raw
error, each requiring them to leave the TUI. Every one of them is already reported by an
existing endpoint with an actionable hint attached. Turning that into one screen with
one-keypress fixes is the entire difference between "it didn't work" and a product.

Everything else in this document is downstream of that. The second-most-valuable thing is
the inbox pre-scan card, and it is valuable for a specific reason: the agent is
prompt-engineered *not* to narrate the scan in prose because it expects the client to draw
a card. A TUI that ignores `render` doesn't degrade gracefully — it turns the flagship
interaction into one vague sentence.

Three things in the current TUI break the bar before email is even involved:

1. **A fresh install lands on an empty screen.** `AllSections()` puts `Installed` first and
   `NewHubModel` opens on tab 0 (`hub/model.go:37,48`). With nothing installed, the first
   thing a new user sees is a 20-line robot and an empty list.
2. **The home screen does not fit an 80x24 terminal.** `resizeList` budgets 26 rows of
   chrome and floors the list at 5 (`hub/model.go:357-362`), so the minimum render is ~31
   rows. Over SSH in a default window it overflows.
3. **Connection state is signalled by color alone.** `RenderStatusBar` draws the same `●`
   for connected and disconnected and changes only the color (`statusbar.go:24-25`).

### What exists today vs. what this design assumes

Every screen below is drawn against real endpoints wherever one exists. These are the
exceptions — assume nothing here is implemented:

| Named in this doc | Status |
|---|---|
| `gaia tui` (and `gaia tui run/install/list/status`) | **Proposed.** No `tui` subparser exists in `src/gaia/cli.py`. Today the Go binary's own cobra root is `Use: "gaia"` (`tui/internal/cli/root.go:15`), so installing it alongside the wheel produces two different `gaia` commands. Naming is open decision D4 in the engineering plan and should be closed before Step 2. |
| `GET /daemon/v1/catalog`, `POST /daemon/v1/agents/{id}/install`, `DELETE /daemon/v1/agents/{id}` | **Proposed** (plan Phase 2). Live daemon routes are `/daemon/v1/status`, `/shutdown`, `/agents`, `/agents/{id}/ensure`, `/agents/{id}/stop`. |
| `GET/PUT /v1/email/config`, `GET/DELETE /v1/email/jobs` | **Proposed** (§6 Tier 3). |
| `POST /v1/email/query/{run_id}/confirm` (approval resume) | **Proposed** (plan Phase 3.2). The `cancel` sibling exists. |
| `~/.gaia/tui/config.json`, `--glyphs` / `GAIA_TUI_GLYPHS` | **Proposed**, TUI-local, no backend. |

Everything else named — `/v1/email/init` (GET and POST), `/connectors`, `/briefing`,
`/search`, `/calendar/events`, `/v1/email/agent/memory`, `/daemon/v1/status`,
`/daemon/v1/agents`, `/v1/email/query/{run_id}/cancel` — exists today.

---

## 1. Journey map

| Stage | User's goal | Friction today | The fix |
|---|---|---|---|
| 1 Discovery | "What is this and what can it do?" | Opens on an empty `Installed` tab; email listed as `Available` but has no binary and no installer | Open on a single unified list with status badges; email says what it needs before you pick it |
| 2 Install | "Get it, and tell me how long" | No install path outside the web UI; lazy fetch is dead (placeholder SHAs) | Daemon-side install with size, progress, and a resumable failure |
| 3 Preflight | "Is it going to work?" | Four walls, hit one at a time, each a raw error | One readiness screen, one keypress per fix, never leaves the TUI |
| 4 Connect mailbox | "Let it read my mail" | Obtain a Google OAuth client id + secret, paste both into a terminal, hope the browser opens | Can't be fixed in v1 — make it a guided 4-step flow with copy-paste fallbacks and a visible timeout. Replacement filed as #2469 |
| 5 First task | "Triage my inbox" | Agent deliberately emits no prose; a client without the card shows one shrug of a sentence | The pre-scan card, with numbered rows that are actionable |
| 6 Approvals | "Don't send that without asking" | CLI path auto-approves all nine gated tools (`console.py:219`); canonical `/query` can't resume | Modal showing the literal payload, decidable in two keystrokes, deny-by-default on timeout |
| 7 Autonomous | "Work while I'm not looking, don't spam me" | Nothing surfaces background work; jobs are listable only by asking the agent in English | A one-line ledger in the status bar and an activity screen; "what happened while you were away" on launch |
| 8 Day 5 | "What needs me?" | Empty chat box | Home screen shows the last briefing and pending jobs before the user types |
| 9 Settings | "Change how it behaves" | Two settings have an API; the other ~20 have no persistence at all | Ship the two, deep-link the rest into chat, don't fake the ones that won't stick |
| 10 Errors | "What do I do now?" | Errors surface as HTTP status or a traceback | Every failure gets a remedy line and a key. Ladder ported from `playground_html.py:358` |
| 11 Quit / uninstall | "Remove it cleanly" | `q` silently leaves the daemon running; `d` removes a catalog row in memory and nothing on disk | Say what keeps running on exit; on uninstall enumerate what is deleted, what survives, and what breaks (pending scheduled sends) |

---

## 2. Design rules

These are the constraints turned into things you can check a screen against.

**R1 — 80x24 is the target, not the floor.** Every screen must render its primary content
in 24 rows at 80 columns. The robot logo collapses to a three-line wordmark below 30 rows.
Any list gets at least 8 rows.

**R2 — No color-only signals.** Every state carries a text token: `[ok]`, `[..]`, `[!]`,
`[--]`. Color is redundant reinforcement. This replaces the status-bar dot.

**R3 — ASCII by default, glyphs by opt-in.** The current chat live region uses 🧠, 🔧, 🎯,
⚠️ (`chat/model.go:703-716`) and the catalog carries per-agent emoji. Emoji width is
unreliable in terminals and breaks lipgloss's width math. Default to ASCII tokens; keep a
`--glyphs` flag / `GAIA_TUI_GLYPHS=1` for users who want them.

**R4 — The chat input owns every printable key.** No bare-letter shortcuts on the chat
screen, ever. TUI actions there are slash commands, `ctrl+` chords, or bare keys *only*
inside an explicit focus mode entered with `tab`.

**R5 — A failure is never a dead end.** Every error state names what failed, what to do,
and the key that does it. If the remedy genuinely requires leaving the TUI, the exact
command is on screen and copyable.

**R6 — Agent-agnostic unless justified.** Cards render from `render` + `data`, not from
tool names. Where this document proposes email-specific UI, it is marked
**[email-specific]** with the reason.

---

## 3. Stage by stage

### Stage 1 — Discovery

`gaia tui`, nothing installed. Goal: know that email exists, what it does, and what it will
cost to try.

**Change: drop the tab-per-status model for a single list with status badges.** Tabs make
sense when each tab has content. With one installable agent in the catalog, `Installed` is
empty, `Available` has one row, and `Coming Soon` has three. That is a filing cabinet for
an empty office. One list, sorted installed-first, with a badge column, shows the whole
world at once and removes two keystrokes from the critical path.

Populated (>= 30 rows):

```
   ▄▄▄▄▄▄▄                GAIA  ·  Local AI Agent Hub  ·  by AMD
  █ ▄   ▄ █
  █  ▀▀▀  █               Agents run on your machine. Nothing leaves it.
   ▀▀▀▀▀▀▀

  Agents                                        1 installed · 0 running
  ────────────────────────────────────────────────────────────────────────
  > [ok]   Email          Triage, draft, calendar          v0.5.0  ready
    [--]   Code           Code generation                          not out
    [--]   Analyst        CSV / Excel analysis                     not out
    [--]   Browser        Web research                             not out

  ────────────────────────────────────────────────────────────────────────
  enter run · i install · s settings · / search · ? help · q quit
```

Empty (nothing installed — the actual first run):

```
   ▄▄▄▄▄▄▄                GAIA  ·  Local AI Agent Hub  ·  by AMD
  █ ▄   ▄ █
  █  ▀▀▀  █               Agents run on your machine. Nothing leaves it.
   ▀▀▀▀▀▀▀

  Agents                                       0 installed · 0 running
  ────────────────────────────────────────────────────────────────────────
  > [ ]    Email          Triage, draft, calendar          v0.5.0    38 MB
             Reads and sorts your inbox, drafts replies, handles
             calendar invites. Needs: a Gmail or Outlook account,
             and about 4 GB of disk for the local AI model.

    [--]   Code           Code generation                          not out
    [--]   Analyst        CSV / Excel analysis                     not out

  ────────────────────────────────────────────────────────────────────────
  i install Email · / search · ? help · q quit
```

The selected row expands to three lines of "what it does / what it needs". That is the
answer to "how does someone know email exists and what it needs" and it costs one field on
the catalog entry (`requirements: []string`).

Degraded — 80x24 (logo collapses, description collapses to one line):

```
  G A I A  ·  Local AI Agent Hub                0 installed · 0 running
  ────────────────────────────────────────────────────────────────────────
  > [ ]    Email        Triage, draft, calendar        v0.5.0     38 MB
             Needs a Gmail/Outlook account + ~4 GB for the local model
    [--]   Code         Code generation                          not out
    [--]   Analyst      CSV / Excel analysis                     not out
    [--]   Browser      Web research                             not out
  ────────────────────────────────────────────────────────────────────────
  i install Email · / search · ? help · q quit
```

Loading — the catalog is a network call, so the first frame must not be blank. The rows
that are known locally (installed agents, from the `.installed` sentinels) render
immediately; the rest fill in:

```
  G A I A  ·  Local AI Agent Hub                        [..] loading list
  ────────────────────────────────────────────────────────────────────────
  > [ok]   Email          Triage, draft, calendar          v0.5.0  ready

    [..]   fetching the agent list from hub.amd-gaia.ai
  ────────────────────────────────────────────────────────────────────────
  enter run · / search · ? help · q quit
```

Daemon unreachable. The catalog comes from `GET /daemon/v1/catalog`, which **does not
exist yet** — it is proposed in the engineering plan's Phase 2; only
`/daemon/v1/status`, `/daemon/v1/agents`, and the two agent start/stop routes are live
today:

```
  G A I A  ·  Local AI Agent Hub                            [!] offline
  ────────────────────────────────────────────────────────────────────────
  Can't reach the GAIA background service, so the agent list may be stale.

    Showing the last list I saw (cached 2h ago).

    r  retry            s  start the service
  ────────────────────────────────────────────────────────────────────────
```

**Also fix:** agents the daemon cannot start must not read `Available`. `builtin_specs()`
knows only `email` (`sidecars/spec.py:68`), so anything else the catalog offers would
install and then fail to run. Show them as `not out` until the spec is synthesized from the
installed `gaia-agent.yaml`. "Available and broken" is the exact wall the bar forbids.

---

### Stage 2 — Install

Goal: get the binary, know how big and how long, recover from a failure.

Progress:

```
  ┌─ Installing Email ─────────────────────────────────────────────────┐
  │                                                                    │
  │  Downloading  ██████████████████░░░░░░░░░░░░░  22.4 / 38.0 MB      │
  │               4.1 MB/s · about 4s left                             │
  │                                                                    │
  │  [ok] fetched catalog                                              │
  │  [..] downloading gaia-agent-email 0.5.0 (darwin-arm64)            │
  │  [ ]  verifying checksum                                           │
  │  [ ]  installing to ~/.gaia/agents/email                           │
  │                                                                    │
  │  ctrl+c  cancel                                                    │
  └────────────────────────────────────────────────────────────────────┘
```

Failure — download interrupted:

```
  ┌─ Install failed ───────────────────────────────────────────────────┐
  │  [!] Download stopped after 22.4 of 38.0 MB.                       │
  │                                                                    │
  │  Network dropped, or hub.amd-gaia.ai is unreachable.               │
  │  Nothing was installed; nothing to clean up.                       │
  │                                                                    │
  │  r  try again        d  details        esc  back                   │
  └────────────────────────────────────────────────────────────────────┘
```

Failure — checksum mismatch. Different copy on purpose; this is not a retry-and-hope:

```
  ┌─ Install failed ───────────────────────────────────────────────────┐
  │  [!] The downloaded file doesn't match its published checksum.     │
  │                                                                    │
  │  Refusing to install it. This is either a corrupted download or a  │
  │  tampered file. Retrying is safe; if it fails twice, report it:    │
  │  github.com/amd/gaia/issues                                        │
  │                                                                    │
  │  r  try again        d  details        esc  back                   │
  └────────────────────────────────────────────────────────────────────┘
```

Failure — sidecar still running (mirrors `hub.py:58-81`, which aborts if the pid survives):

```
  │  [!] Email is still running and its files are in use.              │
  │      k  stop it and continue        esc  cancel                    │
```

**"d details" is not optional.** Install is the first thing that can fail on a machine you
can't see. It expands to the install log — the same lines, untruncated, plus the URL, the
target path, and the expected/actual checksum. It is the only debugging tool a remote user
has.

---

### Stage 3 — Preflight / readiness

Goal: know it will work before typing, and fix what won't from here.

**The gate is on the launch path, not a screen you navigate to.** Pressing `enter` on Email
runs the four checks. If they all pass — the common repeat case, because connections live
in a machine-global keyring shared with the web UI and `gaia connectors` — the screen shows
for under a second and the user lands in chat. If any fails, it stays and becomes the fix
surface.

Four sources, all existing: `GET /daemon/v1/status`, `GET /daemon/v1/agents`,
`GET /v1/email/init` (Lemonade reachable + version >= `MIN_LEMONADE_VERSION` + model
present + live `ctx_size`, 200 when ready and 503 when not, `hint` populated on failure),
`GET /v1/email/connectors` (per-provider `connected`, `account_email`, `scopes`,
`can_send`).

Checking:

```
  Getting Email ready
  ────────────────────────────────────────────────────────────────────────

    [ok]  Background service     running (pid 41822)
    [ok]  Email agent            0.5.0, started
    [..]  Local AI               checking Lemonade...
    [  ]  AI model               —
    [  ]  Mailbox                —

  ────────────────────────────────────────────────────────────────────────
  esc  back
```

Blocked — the first failure is focused and carries its own fix key:

```
  Getting Email ready                                     2 of 5 ready
  ────────────────────────────────────────────────────────────────────────

    [ok]  Background service     running (pid 41822)
    [ok]  Email agent            0.5.0, started
  > [!]   Local AI               not running
          GAIA needs a local model server. It runs on your machine;
          no email text ever leaves it.
          f  start it for me            c  copy the start command

    [  ]  AI model               —  (checked after the server is up)
    [  ]  Mailbox                —

  ────────────────────────────────────────────────────────────────────────
  f  fix this  ·  r  re-check  ·  d  details  ·  esc  back
```

Blocked at the model — `POST /v1/email/init` streams provisioning progress, so this is a
progress bar, not an instruction to go run `gaia init` somewhere else:

```
  > [!]   AI model               Gemma-4-E4B-it-GGUF not downloaded
          About 4 GB. Once downloaded it's reused by every GAIA agent.
          f  download now (~4 GB)       c  copy `gaia init`

    ... after f:

  > [..]  AI model               downloading  ███████░░░░░░░  1.8 / 4.0 GB
          Safe to leave this screen — it keeps going.
```

Blocked at the mailbox — the handoff into Stage 4:

```
  > [!]   Mailbox                not connected
          Email can't do anything until it can read a mailbox.
          f  connect Gmail or Outlook          (takes about 3 minutes)
```

Blocked at the grant — connected but the agent isn't allowed to send. `can_send: false`
with `connected: true` is a distinct state and today it surfaces only as a 403 mid-task:

```
  > [!]   Mailbox                you@gmail.com connected, send not allowed
          The account is linked but Email wasn't granted permission to
          send. Reconnecting fixes it and takes about a minute.
          f  reconnect                  d  what permissions it asks for
```

Ready — held for ~800ms, then straight into chat:

```
  Getting Email ready                                            ready
  ────────────────────────────────────────────────────────────────────────
    [ok]  Background service     running (pid 41822)
    [ok]  Email agent            0.5.0, started
    [ok]  Local AI               Lemonade 10.2.1
    [ok]  AI model               Gemma-4-E4B-it-GGUF · 16K context
    [ok]  Mailbox                you@gmail.com (Google) · can send
  ────────────────────────────────────────────────────────────────────────
  Starting Email...
```

**Rules for this screen.** Checks run in dependency order and stop at the first failure —
"model not downloaded" is meaningless if the server is down, and `/v1/email/init` already
orders its own hints that way (version-too-old before model-missing). `d` shows the raw
JSON of the failing probe. `r` re-runs everything. Nothing here is email-specific except
the last row: the first four are `daemon status`, `daemon agents`, and the agent's own
`/init` — any sidecar agent that implements `GET /v1/<agent>/init` gets this screen free.
Mailbox is **[email-specific]** and belongs behind a per-agent "extra checks" hook.

---

### Stage 4 — Connecting a mailbox

Goal: give the agent access to Gmail. This is the worst moment in the product and v1
cannot fix it — the flow requires the user to create their own Google OAuth client and
paste an id and a secret into a terminal (`connector_routes.py:46-57`). The conversational
replacement is #2469 and is out of scope.

What v1 owes the user: honesty about the length, a numbered path, no invisible waiting, and
a way out at every step.

Step 0 — set expectations before they start. Skipping this is why people abandon here:

```
  ┌─ Connect a mailbox ────────────────────────────────────────────────┐
  │                                                                    │
  │  Google requires you to create your own access credentials. It's   │
  │  4 steps and takes about 3 minutes. You'll do it once.             │
  │                                                                    │
  │   1. Create a project + OAuth client in Google Cloud Console       │
  │   2. Paste the client ID and secret here                           │
  │   3. Approve access in your browser                                │
  │   4. Done — GAIA stores the token on this machine only             │
  │                                                                    │
  │  Why: GAIA has no shared cloud app, so the connection is yours,    │
  │  not ours. Nothing is sent anywhere but Google.                    │
  │                                                                    │
  │  enter  start        p  provider: Google ▸ Outlook       esc  back │
  └────────────────────────────────────────────────────────────────────┘
```

Step 1 — the console instructions, with the URL copyable and the exact settings spelled
out. The failure this prevents is a user creating the wrong client type and getting an
opaque `redirect_uri_mismatch` twenty minutes later:

```
  ┌─ Step 1 of 4 · Create a Google OAuth client ───────────────────────┐
  │                                                                    │
  │  Open:  https://console.cloud.google.com/apis/credentials          │
  │         c  copy link                                               │
  │                                                                    │
  │   1. Create (or pick) a project                                    │
  │   2. Enable the "Gmail API" and "Google Calendar API"              │
  │   3. Credentials ▸ Create credentials ▸ OAuth client ID            │
  │   4. Application type:  Desktop app        <- must be Desktop      │
  │   5. Copy the client ID and client secret                          │
  │                                                                    │
  │  enter  I have them        b  back        esc  cancel              │
  └────────────────────────────────────────────────────────────────────┘
```

Step 2 — paste. Secret is masked; both fields validated for shape before the request, so a
typo fails here and not after the browser round-trip:

```
  ┌─ Step 2 of 4 · Paste your credentials ─────────────────────────────┐
  │                                                                    │
  │  Client ID      8271039481-a9f3k2....apps.googleusercontent.com    │
  │  Client secret  ****************************                       │
  │                                                                    │
  │  Stored in your system keyring on this machine. Never uploaded.    │
  │                                                                    │
  │  tab  next field     enter  continue     b  back     esc  cancel   │
  └────────────────────────────────────────────────────────────────────┘
```

Step 3 — the browser wait. `POST .../configure` returns `{flow_id, authorization_url}`; the
framework opens the browser itself and stands up a loopback listener. Over SSH there is no
browser, so the URL is on screen from the first frame, not revealed after a failure:

```
  ┌─ Step 3 of 4 · Approve in your browser ────────────────────────────┐
  │                                                                    │
  │  [..] Waiting for you to approve...                        1:47    │
  │                                                                    │
  │  I opened your browser. If nothing appeared, open this yourself:   │
  │                                                                    │
  │    https://accounts.google.com/o/oauth2/v2/auth?client_id=8271     │
  │    03948...&scope=gmail.readonly+gmail.send+calendar&redirect_     │
  │    uri=http%3A%2F%2Flocalhost%3A8765                               │
  │                                                                    │
  │    c  copy the full link                                           │
  │                                                                    │
  │  Google will ask for: read mail, send mail, manage calendar.       │
  │  You'll see an "unverified app" warning — that's your own client.  │
  │                                                                    │
  │  esc  cancel                                                       │
  └────────────────────────────────────────────────────────────────────┘
```

The countdown runs **down** from 2:00 because the underlying wait has a hard timeout; a
spinner with no number is the single most abandonment-prone frame in the product. The
"unverified app" pre-warning matters: it is a red full-page interstitial in Google's UI and
users read it as "this software is malicious".

Step 3 timeout:

```
  │  [!] Gave up waiting after 2 minutes.                              │
  │                                                                    │
  │  Common causes: the browser page was closed, approval was denied,  │
  │  or this machine has no browser (SSH). The link below still works  │
  │  from any machine that can reach this one on localhost:8765.       │
  │                                                                    │
  │  r  wait again      c  copy link      b  back to step 2      esc   │
```

Step 4 — done, with the account and the resulting permission stated plainly:

```
  ┌─ Step 4 of 4 · Connected ──────────────────────────────────────────┐
  │  [ok] you@gmail.com                                                │
  │       Email can read, send, archive, and manage your calendar.     │
  │       Revoke any time: settings ▸ accounts, or in your Google      │
  │       account's security page.                                     │
  │                                                                    │
  │  enter  start triaging                                             │
  └────────────────────────────────────────────────────────────────────┘
```

**Ambiguous provider.** Both Google and Microsoft connected: the picker is presented once
at step 0, and once connected, a mailbox row appears per provider in settings. Mid-task
ambiguity ("which mailbox?") is the agent's job, not a modal — it already has a mailbox
target guard.

---

### Stage 5 — First real task: "triage my inbox"

Goal: see what needs attention. This is the flagship moment.

Chat, empty state. The suggestions are not decoration — with a local model and no prior
context, an open text box produces a bad first query, and the agent already ships a
`CONVERSATION_STARTERS` list (`agent.py:281`):

```
  GAIA │ Email                                       you@gmail.com · ready
  ────────────────────────────────────────────────────────────────────────

    Ready. Everything runs on this machine.

    Try:
      1  Triage my inbox
      2  Which of my sent emails are still waiting on a reply?
      3  Draft a reply to my most recent message
      4  Show me today's calendar

    Press a number, or just type. /help for commands.

  ────────────────────────────────────────────────────────────────────────
  > Ask anything...
  [ok] Email · connected                          tab focus · ctrl+c quit
```

Working. See §5 for why this is five lines and not two:

```
  ▶ You: triage my inbox

    [..] Scanning inbox                                             0:34
         read 25 messages
         classified 25 · urgent 2 · needs reply 7 · archive 12
         applying your preferences
         └ still working — local model, usually 60-90s
```

The card. This is the payload from `render: "email_pre_scan"` on `tool_result`:

```
  ┌─ Inbox · 25 scanned · 0:51 ────────────────────────────────────────┐
  │                                                                    │
  │  URGENT                                                       2    │
  │   1  Sarah Chen             Prod incident follow-up                │
  │      asked for a reply by Friday                                   │
  │   2  billing@vendorco.com   Invoice 4471 past due                  │
  │      payment date has passed                                       │
  │                                                                    │
  │  NEEDS A REPLY                                            3 of 7   │
  │   3  Marcus Webb            Re: Q3 roadmap review                  │
  │      direct question to you                                        │
  │   4  recruiting@acme.io     Interview times for Thursday           │
  │      asked you to pick a slot                                      │
  │   5  Priya N.               Re: contract redlines                  │
  │      waiting on your sign-off                                      │
  │      +4 more — press m                                             │
  │                                                                    │
  │  SUGGESTED ARCHIVE                                       4 of 12   │
  │   6  news@substack.com      Weekly digest #212                     │
  │   7  offers@retailer.com    48-hour sale                           │
  │   8  no-reply@social.com    3 new notifications                    │
  │   9  updates@saas.io        Product changelog                      │
  │      +8 more — press m                                             │
  │                                                                    │
  │  6 informational, not listed.                                      │
  │  Using your priority senders: Sarah Chen, Priya N.                 │
  └────────────────────────────────────────────────────────────────────┘
   tab to act:  1-9 open · a archive · A archive all 12 · r reply · m more
```

The footer lists **focus-mode** keys (§4). They are inert until `tab` moves focus off the
input — rule R4 admits no exceptions on a screen with a text box.

Design decisions in that card, each with a reason:

- **Counts are honest.** `totals` carries the pre-cap numbers; the header reads `3 of 7`,
  never a bare `3`. Silently showing a capped list as if it were complete is the failure
  the `totals` field exists to prevent.
- **The reason line is mandatory.** `why` on urgent/actionable rows, `reason` on archive
  rows (the card reads `reason ?? why`). A row without a rationale is a claim; with one it
  is an argument the user can check. This is what makes a local 4B model's output
  trustworthy.
- **Rows are numbered and actionable.** `1`-`9` opens a row; `a` archives the focused row;
  `A` archives the whole suggested-archive bucket. Without this the card is a report you
  then have to describe back to the agent in English, which is slower than the mail client
  the user already has.
- **`preferences_applied` is shown when non-empty.** It is the only visible evidence the
  agent is learning, and it is the hook for "stop treating X as urgent".
- **No color-only categories.** Section headers are words.
- **Height is bounded.** 3 sections × up to 5 rows, `+N more` beyond. The contract's
  500-item cap rule applies but 500 rows in a terminal is not a card, it is a scroll trap.

Actions taken from the card go back through the agent as a normal turn, with the composed
query shown before it is sent:

```
  ▶ You: archive the Substack weekly digest #212
    [ok] archived                                    ctrl+z undo (28s)
```

**The undo affordance is generic, not email-specific.** Rule: if a `tool_result.data`
carries `undo_window_seconds` and any `*_id` handle, the TUI shows `ctrl+z undo (Ns)` and
counts down. `EmailArchiveResponse` supplies exactly that (`batch_id` +
`undo_window_seconds: 30`). Any agent that returns those two field names gets undo free.

Empty inbox:

```
  ┌─ Inbox · 25 scanned · 0:44 ────────────────────────────────────────┐
  │  Nothing needs you.                                                │
  │  25 messages scanned · 0 urgent · 0 waiting on a reply             │
  │  19 informational, 6 already archived by your rules.               │
  └────────────────────────────────────────────────────────────────────┘
```

Multi-mailbox partial failure — `mailbox_errors[]` comes back free with the scan and today
surfaces nowhere:

```
  │  [!] Outlook wasn't scanned: token expired.                        │
  │      tab then f  reconnect Outlook   (Gmail results below are OK)  │
```

Error mid-turn — remedy ladder, not a status code (§Stage 10).

---

### Stage 6 — Approvals

Goal: say yes safely in under two seconds, or catch the one thing that would have been a
disaster.

Reversible actions (archive, label, mark-read, trash, snooze) run without asking and offer
undo. Nine tools always prompt: `send_draft`, `send_now`, `schedule_send`,
`forward_message`, `permanent_delete`, `accept_invite`, `decline_invite`,
`create_event_from_email`, `quarantine_phishing_message` (`agent.py:296`).

The approval modal answers four questions before the user's finger moves: what will happen,
to whom, with what content, from which account.

```
        ┌─ Send this email? ────────────────────────────────────┐
        │                                                       │
        │  To       sarah.chen@example.com                      │
        │  From     you@gmail.com  (Google)                     │
        │  Subject  Re: Prod incident follow-up                 │
        │                                                       │
        │  Hi Sarah — I've read through the incident report.    │
        │  The root cause looks like the retry loop in the      │
        │  ingest worker. I can have a fix out by Thursday      │
        │  and will send you the postmortem draft before...     │
        │  ── 9 more lines · space to scroll ──                 │
        │                                                       │
        │  This sends real mail and can't be unsent.            │
        │                                                       │
        │   y  send        n  don't send        auto-deny in 47s │
        └───────────────────────────────────────────────────────┘
```

- **The literal payload, never a paraphrase.** The confirmation carries the real recipient,
  subject and body; showing the model's own summary of what it is about to send defeats the
  purpose of the gate.
- **`n` is the default and the timeout denies.** The server's 60s expiry already denies;
  the countdown makes that visible rather than a surprise. `esc` = `n`.
- **`y`/`n` only — no focused-button dance.** The existing `ConfirmModel` supports `y`/`n`
  directly (`confirm.go:75-83`) and defaults focus to No. Keep both.
- **Recipient is verified against the connected account** and flagged when the domain
  differs from every prior recipient — the "sent to the wrong Sarah" class of error.

Destructive-but-local variant (`permanent_delete`) states irreversibility differently
because the risk is different:

```
        │  Permanently delete 3 messages?                       │
        │    · "48-hour sale" — offers@retailer.com             │
        │    · "3 new notifications" — no-reply@social.com      │
        │    · "Weekly digest #212" — news@substack.com         │
        │                                                       │
        │  These skip Trash. They cannot be recovered.          │
        │   y  delete        n  keep them        auto-deny 51s   │
```

**"Always allow" should not ship in v1.** The engineering plan proposes
Always-allow-this-action-this-session (Phase 3.1). Three reasons not to:

1. These nine tools are gated *precisely because* they are irreversible. A session bypass
   converts one model mistake into N mistakes, and the mistakes are outbound email.
2. "This session" has no meaning a user can hold. A TUI stays open for days; the sidecar
   outlives the TUI; the daemon outlives both. Nobody will predict what the scope is.
3. The problem it solves is approval *cost*, and cost is better attacked directly: one
   modal, two keystrokes, no scrolling for the common case. If a real batch case appears
   ("archive these 12"), gate it as **one** approval over a list — which the mockup above
   already does — rather than twelve approvals with an escape hatch.

**If it ships anyway, this is what it must mean.** Scoped to one tool name (`accept_invite`,
not "calendar"), one agent, one *run* — not one session, not one process, and never
persisted to disk. It is offered only for the two tools whose blast radius is a single
calendar row (`accept_invite`, `decline_invite`) and never for `send_draft`, `send_now`,
`schedule_send`, `forward_message`, `permanent_delete`, `create_event_from_email`, or
`quarantine_phishing_message`. The modal states the scope in the label —
`[ a ] allow accept_invite for the rest of this answer` — because "always allow" is a
promise the product cannot keep and should not print.

If a bypass is ever added beyond that, it belongs on the reversible set (already unprompted)
or on a single named batch operation, never as a per-action session flag.

---

### Stage 7 — Autonomous mode

Goal: know something happened, without being interrupted; see and cancel what is pending.

What actually runs unattended today: the one-shot scheduler (on by default,
`scheduler_poll_seconds: 30`) firing scheduled sends and snooze restores, and the daily
briefing (off by default, three env vars, needs a restart).

**Interruptions are banned.** Background work never steals the input line, never opens a
modal, never re-renders the transcript. It changes exactly one thing: a counter in the
status bar.

```
  [ok] Email · connected                    2 background · tab focus · ?
```

The activity screen, reached with `ctrl+g` from chat or `b` from home:

```
  Background activity                                     Email · you@gmail.com
  ────────────────────────────────────────────────────────────────────────
  PENDING
  > [..]  Scheduled send   to marcus@example.com "Re: Q3 roadmap"
          fires tomorrow 08:00                              c  cancel
    [..]  Snoozed          "Invoice 4471 past due"
          returns to inbox tomorrow 09:00                   c  cancel

  RECENT
    [ok]  09:12  Daily briefing ready — 3 urgent            enter  open
    [ok]  08:00  Scheduled send delivered to priya@example.com
    [!]   07:44  Inbox scan skipped — Outlook token expired  f  fix

  ────────────────────────────────────────────────────────────────────────
  c cancel · enter open · r refresh · esc back
```

Empty — the common case, and it must not read as a failure:

```
  Background activity                                     Email · you@gmail.com
  ────────────────────────────────────────────────────────────────────────

    Nothing scheduled and nothing ran since you were last here.

    Background work is off except the daily briefing.
    s  settings ▸ background

  ────────────────────────────────────────────────────────────────────────
  esc back
```

**Cancelling pending work has no REST route.** `list_scheduled_jobs_impl` and
`cancel_scheduled_job_impl` exist only as agent tools (`tools/schedule_tools.py:204,222`),
so today "show me my scheduled sends" is a natural-language round trip through a local LLM.
That is unacceptable for a management screen: it is slow, and it can fail to parse. This
screen needs `GET /v1/email/jobs` and `DELETE /v1/email/jobs/{id}` — two thin routes over
`schedule_store`. Tagged needs-backend, and it is the second-highest-value backend item
after the config file.

**Autonomy is the wrong knob.** The plan's D3 offers `manual | assisted | autonomous`. A
user cannot predict what "assisted" does, and the only real difference between `assisted`
and `autonomous` is whether timers run — approvals are identical by design (G9 requires it).
Replace the enum with the two things a person actually decides:

```
  Approvals   Always ask before sending, deleting, or RSVP-ing.   (fixed)
  Background  [x] Scan the inbox when I open GAIA
              [x] Daily briefing at  08:00
              [ ] Check for unanswered emails I sent   (every 3 days)
```

Same behaviour, no vocabulary to learn, and each checkbox maps to one existing mechanism.

**Background timers do not belong in the TUI.** The plan's Phase 3.3 suggests running a
timer inside the TUI against `pre_scan_inbox` as the cheap path. The TUI is a foreground
app that is closed most of the time; a timer there makes "autonomous" mean "autonomous
while you are watching", which is the one case where the user could just ask. Do the
cheap thing honestly instead: **scan once on launch** (the "welcome back" scan in Stage 8)
and put recurring work in the sidecar where it belongs. A launch scan is simpler than a
timer and produces the entire perceived benefit.

---

### Stage 8 — Day 5

Goal: open the TUI and immediately know what needs attention. Nothing typed.

If exactly one agent is installed and its preflight passes, `gaia tui` skips the hub and
opens that agent with its state already on screen. A hub with one row is a menu with one
item.

```
  GAIA │ Email                                       you@gmail.com · ready
  ────────────────────────────────────────────────────────────────────────

    Welcome back. Since Tuesday:

      [ok]  1 scheduled send delivered
      [!]   1 scan skipped — Outlook token expired      f  fix

    Briefing from 08:12 this morning:
      URGENT           2   Sarah Chen · billing@vendorco.com
      NEEDS A REPLY    7
      SUGGESTED ARCHIVE 12

      1  open the full briefing      2  scan again now

  ────────────────────────────────────────────────────────────────────────
  > Ask anything...
  [ok] Email · connected                  2 background · tab focus · ? help
```

The briefing summary comes from `GET /v1/email/briefing` and renders through the same card
component as the live scan — same envelope, byte for byte. No briefing yet (404) collapses
to a single line offering `s scan now`, not an error.

Honest limitation: **there is no event log the TUI can read**, so "since Tuesday" is
assembled from the briefing timestamp, `schedule_store` job outcomes, and the action log.
Anything that happened and left no row is invisible. Do not fake a fuller history.

---

### Stage 9 — Settings

Goal: change behaviour. Constraint: exactly two settings have a working API — the memory
toggle (`POST /v1/email/agent/memory`) and connectors. `EmailAgentConfig` is an in-memory
dataclass constructed fresh every time, and session creation passes it zero kwargs
(`agent_routes.py:272-274`).

**Do not build a settings screen that writes settings that don't stick.** A control that
silently forgets is worse than no control: the user changes it, observes no effect, and
concludes the product is broken. Ship three groups, each honest about where it lives.

```
  Settings                                                        Email
  ────────────────────────────────────────────────────────────────────────
  ACCOUNTS
  > Google       you@gmail.com · can send            enter manage
    Outlook      not connected                       enter connect

  THIS AGENT
    Memory       on — learns your senders and habits    space toggle
                 Off means no personalization and nothing stored.

  ASK THE AGENT                       these live in conversation, not here
    Priority senders          enter → "who do I treat as priority?"
    Snooze / follow-up rules  enter → "how do you handle follow-ups?"
    Briefing time             enter → "send my briefing at 7am"

  THIS APP
    Glyphs       off — plain ASCII                      space toggle
    Density      compact ▸ full                         space cycle

  ────────────────────────────────────────────────────────────────────────
  enter select · space toggle · esc back
```

- **ACCOUNTS** and **THIS AGENT** are real writes against real endpoints.
- **ASK THE AGENT** deep-links into chat with a composed query. These *are* settable today
  through `preference_tools` — just conversationally. Listing them here solves discovery
  ("where do I set priority senders?") without inventing persistence. It is also the one
  place where a chat-first product should feel proud rather than apologetic.
- **THIS APP** is TUI-local state in `~/.gaia/tui/config.json`, owned by the TUI, no
  backend needed.

A write that fails must revert the visible state, not leave a lie on screen:

```
    Memory       on — learns your senders and habits    space toggle
    [!] Couldn't turn memory off — the agent didn't respond.
        Still on. r retry · d details
```

**What this costs.** Model choice, context size, undo window, follow-up window, briefing
time-of-day, autonomy defaults, and priority-sender *lists* remain unsettable from a real
control. Users will look for them. The unblocker is one file and two routes —
`~/.gaia/agents/email/config.json` with `GET/PUT /v1/email/config`, read at agent
construction, with `restart_required: true` in the response where it applies. That is the
highest-value backend item in the whole port after the readiness gate, and it is what turns
this screen from three groups into the real thing.

---

### Stage 10 — Errors and dead ends

Every failure gets a plain first line, a remedy, and a key. The ladder is a direct port of
`diagnose()` (`playground_html.py:358-367`) — specific causes before generic ones — and
lives in one Go function used by preflight, chat, and the activity screen alike.

| Failure | What the user sees | Key |
|---|---|---|
| Lemonade down | `Local AI isn't running. GAIA needs it to read your mail — it runs on this machine.` | `f` start it · `c` copy the start command (resolved per machine, never hardcoded) |
| Lemonade too old | `Local AI is version 10.0.1; Email needs 10.2.0 or newer.` | `f` upgrade · `c` copy `gaia init` |
| Model missing | `The AI model isn't downloaded yet (about 4 GB, reused by every agent).` | `f` download here · `c` copy `gaia init` |
| Model list unreadable | `Local AI answered but its model list couldn't be read. It may still be starting.` | `r` re-check · `d` details |
| Daemon down | `The GAIA background service isn't running.` | `f` start it |
| Daemon version mismatch | `This TUI needs GAIA 1.1 or newer; the service is 1.0. Update GAIA, then reopen.` | `c` copy `pip install -U amd-gaia` |
| Sidecar not installed | `Email isn't installed on this machine.` | `i` install |
| No mailbox | `Email can't do anything until a mailbox is connected. About 3 minutes, once.` | `f` connect |
| Missing grant | `you@gmail.com is connected but Email isn't allowed to send. Reconnecting fixes it.` | `f` reconnect · `d` permissions |
| Revoked token | `Google revoked access to you@gmail.com — this happens after a password change or 6 months idle.` | `f` reconnect |
| Ambiguous provider | `Two mailboxes are connected. Which one?` → inline picker | `1`/`2` |
| Turn ended without `final` | `The agent stopped mid-answer. Nothing was sent or deleted.` | `r` retry · `d` details |
| Model context exceeded | `That was too much for the model's context window. Try a narrower question (e.g. "urgent only").` | `e` edit and retry |

Two rules that matter more than the table. **A stream that ends without `final` or `error`
is a failure, not a success** — the contract mandates exactly one terminal event, and
silently rendering a partial answer as complete is how a user believes mail was sent when
it wasn't. And **`d details` is present on every error**, expanding to the raw event or HTTP
body. Anything less makes remote debugging impossible.

---

### Stage 11 — Quitting and uninstalling

**Quitting is not uninstalling, and the difference must be visible.** `q` closes the TUI;
the daemon and the sidecar keep running, on purpose — that is what makes scheduled sends
fire and the briefing appear. A user who assumes `q` stops everything and later finds a
scheduled email was delivered has been surprised by their own tool. State it once, on exit:

```
  Closed. GAIA keeps running in the background so scheduled sends and your
  briefing still work.   Stop it with:  gaia daemon stop
```

One line, printed to stdout after the alt-screen is torn down. Not a modal.

Goal for uninstall: remove it and know exactly what that means.

The current binding is `d`, `delete`, **or** `backspace` (`hub/model.go:137`) and it only
mutates the in-memory catalog. Two changes: make it real, and **drop `backspace` and
`delete`** — `backspace` is a universal "go back" reflex and pointing it at an uninstall
dialog is a trap.

```
  ┌─ Uninstall Email? ─────────────────────────────────────────────────┐
  │                                                                    │
  │  Deletes                                                           │
  │    · the agent program            ~/.gaia/agents/email    38 MB    │
  │    · what it learned about you    memory database          2 MB    │
  │                                                                    │
  │  Keeps                                                             │
  │    · your Google connection — other GAIA apps still use it         │
  │    · the AI model — shared with every agent               ~4 GB    │
  │                                                                    │
  │  [!] 2 scheduled sends will never be delivered:                    │
  │        "Re: Q3 roadmap" to marcus@example.com — tomorrow 08:00     │
  │        "Follow-up" to priya@example.com — Friday 09:00             │
  │                                                                    │
  │  [ ] also disconnect you@gmail.com from GAIA entirely              │
  │                                                                    │
  │   y  uninstall        n  cancel        space  toggle the box       │
  └────────────────────────────────────────────────────────────────────┘
```

Three things this gets right that a generic confirm does not. **Pending scheduled sends are
enumerated** — silently dropping a send the user approved is a broken promise, and the data
is in `schedule_store`. **The keyring connection survives by default and says so**, because
it is machine-global and shared with the web UI and `gaia connectors`; deleting it as a
side effect would break other surfaces. **Disconnecting is offered but opt-in**, because a
live Google grant left behind after an uninstall is a genuine privacy surprise.

Done state names what is left:

```
  │  [ok] Email removed.                                               │
  │       Your Google connection and the AI model are still here.      │
  │       Reinstall any time with  i .                                 │
```

---

## 4. Keybinding map

Checked against what `hub/model.go`, `chat/model.go`, and `components/confirm.go` already
bind. Existing bindings are marked; conflicts and required changes are called out.

### Global

| Key | Action | Note |
|---|---|---|
| `ctrl+c` | Quit (cancel first if streaming) | existing, both screens |
| `?` | Help overlay | existing on hub; **must not** be bare on chat (R4) |
| `esc` | Leave the current context, one level | see ladder below |

### Home / hub

| Key | Action | Status |
|---|---|---|
| `↑ ↓` `j` `k` | Move selection | existing (bubbles list) |
| `enter` | Run the selected agent (via preflight) | existing |
| `/` | Search | existing |
| `i` | Install | new — free |
| `d` | Uninstall | existing; **drop `delete` and `backspace` aliases** |
| `s` | Settings | new — free |
| `b` | Background activity | new — free |
| `r` | Refresh catalog | **repurposed** from "request an agent" |
| `v` | Vote for a coming-soon agent | existing |
| `q` | Quit | existing |
| `tab` / `shift+tab` | (removed with the tab bar) | freed |

`r` currently prints "Agent requests coming soon". A live-refresh key earns the slot on a
screen whose contents now come from a network call; agent requests move to `?` help.

### Chat

Rule R4: the input owns every printable key. Everything below is a chord, a slash command,
or lives inside focus mode.

| Key | Action | Status |
|---|---|---|
| `enter` | Send | existing |
| `alt+enter` | Newline | existing binding is a no-op; make it insert a newline |
| `esc` | Cancel stream → exit focus mode → back to hub | existing (first and last); middle rung is new |
| `pgup` / `pgdn` | Scroll transcript | existing |
| `tab` | Enter focus mode on the newest card | new — **requires disabling the textarea's tab handling** |
| `ctrl+z` | Undo the last reversible action, while its window is open | new — free |
| `ctrl+g` | Background activity | new — free |
| `ctrl+r` | Expand / collapse the work log of the last turn | new — free |
| `ctrl+l` | Clear transcript (same as `/clear`) | new — free |
| `1`-`9` | Send suggestion N — **only when the input is empty and no messages yet** | new, contextual |

**The textarea owns more chords than the GAIA code does.** `bubbles/textarea`'s default
keymap reserves `ctrl+a e f b n p k u w v d h m t` and most `alt+` letters, and
`chat/model.go:323-327` forwards every unhandled key straight into it. Checking only
`chat/model.go` for collisions is not enough — the chords above are free *after* excluding
that keymap. `ctrl+g`, `ctrl+l`, `ctrl+r`, `ctrl+z` are the safe ones used here; avoid
`ctrl+s` / `ctrl+q`, which terminals swallow as flow control.

Slash commands: `/help` `/hub` `/clear` exist. **`/init` must be removed or wired** — today
it prints "Initializing `<agent>`..." and does nothing (`chat/model.go:299-305`). Point it at
the preflight screen. Add `/settings` and `/accounts`.

### Focus mode (inside a card)

Entered with `tab`, exited with `esc` or `tab`. The input is dimmed and bare keys are safe.

| Key | Action |
|---|---|
| `↑ ↓` `j` `k` | Move between rows |
| `1`-`9` | Jump to row N |
| `enter` | Open / expand the row |
| `a` | Act on the row (archive, for a scan card) |
| `A` | Act on the whole section |
| `r` | Reply to the row |
| `m` | Show the rest of the truncated section |
| `f` | Fix — on an error row inside a card (reconnect, retry) |
| `esc` `tab` | Back to the input |

Any bare key printed inside a card is a focus-mode key and does nothing until `tab` is
pressed; card footers say so (`tab to act: ...`). The one carve-out is `1`-`9` on the
welcome and day-5 screens, which are live while the input is empty *and* the transcript is
empty — the only moment where a digit cannot be the start of a real query.

Per-row verbs are declared by the card renderer, so a `table` card gets only navigation and
`enter`, while the scan card adds `a`/`A`/`r`. **[email-specific]** in that the verbs come
from a per-render-key table; the mechanism is generic.

### Modals (approval, confirm, install)

| Key | Action | Status |
|---|---|---|
| `y` | Yes | existing |
| `n` / `esc` | No — the default | existing |
| `←` `→` `tab` | Move between buttons | existing |
| `enter` | Activate focused button | existing |
| `space` | Scroll a long payload | new, approval modal only |
| `d` | Details | new |

### The `esc` ladder

One key, one rule: leave the innermost thing. Modal → focus mode → streaming turn → screen.
Each rung must be visibly distinct or `esc` becomes a gamble; the status bar always names
what `esc` will do right now (`esc cancel`, `esc back to input`, `esc back to agents`).

---

## 5. Information density and layout

**Argument: keep a single pane, and reject the current 2-line live region for anything that
touches more than a handful of items.**

### Single pane, no splits

No sidebar, no split panes, no inbox list beside the chat. At 80 columns a split leaves ~38
usable columns per side, which is too narrow for a subject line plus a sender. Cards render
inline in the transcript at full width. This also keeps the TUI agent-agnostic: a layout
built around a persistent message list is a mail-client layout, and the next agent would
have to fight it.

### The live region must grow

Today all tool activity collapses into two lines — the latest step and the latest action
(`chat/model.go:660-693`) — and everything else is discarded. For a bash agent running one
command that is right. For an email turn it is wrong for three reasons:

1. **A triage turn takes 60-90s on a local 4B model and touches dozens of messages.** Two
   static lines are indistinguishable from a hang, and the user's next move is `ctrl+c`.
2. **The work is the evidence.** "Read 25 · classified 25 · applied your preferences" is
   what makes the card believable. Discarding it discards the audit trail.
3. **Repetition is information.** Twenty `triage_message` calls collapsed to a single
   flickering line reads as one slow call.

Proposal — a bounded, self-collapsing work log:

```
    [..] Scanning inbox                                             0:34
         read 25 messages
         classified 25  (urgent 2 · reply 7 · archive 12)
         applying your preferences
         └ still working — local model, usually 60-90s
```

- **Cap at 5 lines**, oldest scrolling out. Never unbounded.
- **Collapse repeats by tool name with a counter**: `triage_message ×14`.
- **A "still working" line after 20 seconds** with the expected duration. This single line
  removes most premature cancellations.
- **On completion the whole log collapses to one summary line**: `4 tools · 51s · ctrl+r to
  show`. The transcript stays readable; the evidence stays reachable.

All of this is agent-agnostic and fixes the general "is it stuck?" problem, not an email
one.

### Cards

Bounded height (≈20 rows), `+N more` beyond, `m` to expand. Full width minus 4. One card per
`tool_result` carrying a `render`, drawn inline where it occurred so the ordering of work
and results is preserved.

### Generic primitives — build three, not four or five

The contract defines five (`table`, `key_value`, `list`, `image`, `diff`); the engineering
plan says four. Build **three**:

- `table`, `key_value`, `list` — real, immediately useful, zero backend work for any agent.
- `image` — cannot render in a terminal. Degrade to `[image: <caption>] (not shown in
  terminal)`. Do not build a sixel/kitty path in v1.
- `diff` — no producer emits one today. Skip until one does; the unsupported-card fallback
  covers it.

And implement the fallback rules exactly as specified: an unknown `render` shows
`Unsupported card: "<key>"` with a raw dump; a schema-invalid payload shows
`Invalid <key> payload` with a raw dump. **Never blank, never silent.** The current TUI
drops unparsed events entirely, which is how a turn appears to do nothing.

### Status bar

Replace the color-only dot (R2) and carry the four things the user needs continuously:

```
  [ok] Email · you@gmail.com          2 background · esc back · ? help
```

---

## 6. Prioritized UX improvements

Tagged **[now]** (possible against existing APIs) or **[backend]** (needs a route or a
config file first).

### Tier 1 — the difference between broken and working

| # | Improvement | Tag |
|---|---|---|
| 1 | Preflight/readiness gate on the launch path, wired to `/daemon/v1/status`, `/daemon/v1/agents`, `/v1/email/init`, `/v1/email/connectors` | **[now]** |
| 2 | Inbox pre-scan card from `render: "email_pre_scan"` | **[now]** |
| 3 | Error remedy ladder ported from `diagnose()` — one function, used everywhere | **[now]** |
| 4 | Approval modal showing the literal payload, deny-by-default, visible countdown | **[now]** for the transport that supports resume |
| 5 | First-run empty state: unified agent list, requirements shown before install | **[now]** |
| 6 | Guided 4-step connector flow with the auth URL visible from frame one and a counting-down timeout | **[now]** |

### Tier 2 — makes it feel finished

| # | Improvement | Tag |
|---|---|---|
| 7 | Model provisioning inside the TUI via streaming `POST /v1/email/init` | **[now]** |
| 8 | Bounded work log + "still working" hint + `ctrl+r` collapse | **[now]** |
| 9 | Generic `table` / `key_value` / `list` renderers with the mandated fallbacks | **[now]** |
| 10 | Generic undo affordance keyed on `undo_window_seconds` + an id handle | **[now]** |
| 11 | Briefing on launch via `GET /v1/email/briefing`; 404 → `s scan now` | **[now]** |
| 12 | 80x24 layout fixes: collapsing logo, list floor of 8, correct chrome budget | **[now]** |
| 13 | Text status tokens replacing color-only signals; ASCII default glyph set | **[now]** |
| 14 | Uninstall dialog that enumerates deletions, survivors, and pending sends | **[now]** (pending-send list needs `schedule_store` read → **[backend]** if not exposed) |
| 15 | Triage detail panel ported from `renderTriage` — category, spam/phishing, summary, action items | **[now]** |

### Tier 3 — real, but after the above

| # | Improvement | Tag |
|---|---|---|
| 16 | `~/.gaia/agents/email/config.json` + `GET/PUT /v1/email/config` | **[backend]** |
| 17 | `GET /v1/email/jobs` + `DELETE /v1/email/jobs/{id}` for the activity screen | **[backend]** |
| 18 | Settings screen (accounts + memory + deep-links + app prefs) | **[now]** for what it ships; grows with 16 |
| 19 | Inbox table + calendar list from `POST /v1/email/search` and `GET /v1/email/calendar/events` | **[now]** |
| 20 | Row-level actions dispatched as fixed-function REST calls instead of agent round trips | **[backend]** — faster, but email-specific; not v1 |
| 21 | Cards for follow-ups, scheduled jobs, sender profiles | **[backend]** — cheapest via the generic `table` key |

**Seven of the top ten need no backend change at all.** That is the shape of this port: the
data already exists and is not being drawn.

---

## 7. Disagreements with `email-agent-tui-port.md`

**D-a. Preflight is not "Phase 5.1, do it early" — it is Phase 1's acceptance criterion.**
The plan sequences the readiness screen as a UX polish item that "should jump the queue".
That undersells it. Without it, every failure mode of the new transport — daemon down,
token rotated, version gate failed, model missing — reaches the user as an HTTP status.
Phase 1 is not done when the SSE stream parses; it is done when a stream that *cannot* start
explains itself. Merge 5.1 into Phase 1 and make it the exit test.

**D-b. Do not ship "always allow" (Phase 3.1).** Reasoning in Stage 6. Short version: the
nine gated tools are gated because they are irreversible, "this session" has no boundary a
user can hold in a long-lived TUI, and the actual problem — approval cost — is better solved
by a two-keystroke modal and by batching a multi-item action into one approval.

**D-c. Replace the three-level autonomy enum (D3) with two plain controls.** `manual |
assisted | autonomous` is implementer vocabulary. Approvals are identical across
`assisted` and `autonomous` by design (G9), so the enum's only real axis is "do timers
run". Ship "always ask before irreversible things" as fixed behaviour and a short checklist
of background jobs. Same code, no vocabulary to teach, and it does not promise a goal engine
that does not exist.

**D-d. Do not put background timers in the TUI (Phase 3.3).** The plan calls the TUI-timer
version "materially cheaper and 80% of the perceived value". It is cheaper, but the value
claim is wrong: the TUI is closed most of the time, so a timer inside it means "autonomous
while you watch" — exactly the case where asking is easier. Do the launch-time scan
instead. It is simpler than a timer, and it is what produces the day-5 screen.

**D-e. The settings screen (Phase 4.2) is over-scoped for what exists.** Eight rows are
listed; two have persistence. Building the other six against an in-memory dataclass ships
controls that silently forget, which is worse than their absence. Ship three groups
(accounts, memory, app-local prefs) plus conversational deep-links for what the agent can
already do in English, and spend the saved budget on the config file (Phase 4.1) that makes
the rest real.

**D-f. `builtin_specs()` cannot be deferred — but it also does not need solving.** The plan
notes the hub "will happily install agents the daemon then refuses to start" and offers
"synthesize, or filter". Filter, explicitly, and label them `not out` rather than
`Available`. Zero cost, and it closes the exact dead end the bar forbids. Synthesis is a
v2 problem that arrives with the second installable agent.

**D-g. Build three generic primitives, not four.** The plan says "implement the four
(`table`, `key_value`, `list`, `diff`)". The contract defines five. `image` is
base64-raster-only and cannot render in a terminal; `diff` has no producer. Build `table`,
`key_value`, `list` and the two fallback behaviours; the fallback covers the rest for free.

**D-h. `/init` in chat is currently a lie and should be in Phase 1's cleanup list.** It
prints "Initializing `<agent>`..." and returns. It is in the same category as the plan's §5
bug list and costs one line to either delete or point at preflight.

**D-i. Add two home-screen fixes to the §5 bug list.** The hub opens on an empty
`Installed` tab on a fresh machine, and its 26-row chrome budget makes it overflow an 80x24
terminal. Both are pre-existing, both are on the first-run path, and both are cheap.

**D-j. `backspace` must stop triggering uninstall.** `d`, `delete`, and `backspace` all open
the uninstall dialog. Once uninstall is real, `backspace` — which every user presses meaning
"go back" — becomes a destructive-action trigger.

**Where the plan is right and this design leans on it:** the transport decision (D1: the
canonical `/query` relay, not the email-specific stateful surface) is correct and is what
lets the approval modal, the cards, and the work log be agent-agnostic. Install belonging to
the daemon (D2) is correct for the same reason. And the §2 landmine analysis — the CLI path
silently auto-approving all nine gated tools — is the single most important finding in
either document.

---

## 8. What we deliberately do not build for v1

| Not building | Why |
|---|---|
| Agent-led conversational OAuth | Needs a first-party OAuth client, mid-run structured input, and probably a device-code flow. #2469. Agreed with the plan. |
| Split panes / persistent inbox sidebar | Unusable at 80 columns, and it turns an agent-agnostic TUI into a mail client. |
| A full mail client (browse, search-as-primary, threading) | The product is triage. `/v1/email/search` gets a card, not a screen. |
| Settings controls for unpersisted config | A control that forgets is worse than a missing one. Ships with the config file. |
| "Always allow" on irreversible actions | Stage 6 / D-b. |
| TUI-side background timers | D-d. Launch-time scan instead. |
| `image` and `diff` render primitives | One cannot render in a terminal, the other has no producer. |
| Mouse support | Breaks the SSH promise and duplicates every keybinding. |
| Themes / color customization | R2 makes color redundant by design; theming is then cosmetic. |
| Desktop notifications, sound, terminal bell | Interruption is the thing Stage 7 is designed to avoid. |
| Multi-account switching UI | One connected mailbox per provider; ambiguity is the agent's job. |
| Agent-request / voting expansion | `v` and `r` occupy prime home-screen slots for features that post an id to a URL. Keep `v`, repurpose `r`. |
| Session history / transcript persistence | The host owns the in-memory transcript per the plan; persisting it is a separate feature with its own privacy surface. |

---

## 9. Build order

Each step is shippable and observable on its own. Steps marked **[backend]** need Python.

**Step 1 — Reachability, honestly reported.**
HTTP/SSE transport + daemon discovery, and the preflight gate as its exit test (D-a).
Includes the error ladder, because the gate is meaningless without it. Done when: a machine
with nothing running shows five checkable rows and never an HTTP status code.

**Step 2 — First run works end to end.**
Install/uninstall against the daemon routes **[backend, in progress]**, unified home screen
with requirements, the 80x24 and color-only fixes, the guided connector flow. Done when: a
person with a fresh machine and a Gmail account reaches an agent prompt without leaving the
TUI or reading a doc.

**Step 3 — The flagship turn.**
Pre-scan card, the three generic primitives with their fallbacks, the bounded work log,
the generic undo affordance. Done when: `triage my inbox` produces the card in Stage 5 and
`ctrl+z` puts an archived message back.

**Step 4 — Safety.**
Approval modal + resume on `/v1/email/query` **[backend]**, and the `console.py`
auto-approve fix, which stands alone as a security fix regardless of the TUI. Done when: an
agent-initiated send cannot happen without a keystroke, and a 60-second silence denies.

**Step 5 — It knows you were away.**
Briefing on launch, the "since `<day>`" summary, the activity screen backed by
`GET /v1/email/jobs` **[backend]**. Done when: opening on day 5 shows what happened and what
is pending before anything is typed.

**Step 6 — Real settings.**
`~/.gaia/agents/email/config.json` + `GET/PUT /v1/email/config` **[backend]**, then grow the
settings screen into it. Done when: a briefing time set in the TUI survives a restart.

**Step 7 — Depth.**
Triage detail panel, inbox/calendar cards, follow-up and job cards via the generic `table`
key. Everything here is optional and none of it blocks the bar.

Steps 1 and 2 are the whole bar: *one command, ends up triaging email, no docs, no
unrecoverable wall.* Step 3 is what makes it worth doing. Steps 4-7 are what makes it worth
keeping.

---

*Registration note: `docs/docs.json` lists only `.mdx` plan pages. This document is `.md`,
matching `email-agent-tui-port.md`, and is deliberately not added to the navigation.*
