# Porting the Email Agent into the TUI

Status: Scoping / not started
Owner: TBD
Related: #1186 (TUI), #2191 (email thin client), #2142 (daemon relay), #555 (autonomy epic)

---

## 1. Where we are today

**The TUI** (`tui/`, Go + Bubble Tea, ~4k LOC, landed whole in `aa2ca33f`) is an agent
browser plus a chat screen. It knows how to do exactly one thing: launch a local
executable and trade newline-delimited JSON with it over stdin/stdout
(`tui/internal/client/subprocess.go`). Its agent list is a hardcoded Go slice
(`tui/internal/catalog/catalog.go:179`). Email is already in that list at
`catalog.go:231` — marked `StatusAvailable` with no binary, i.e. visible but dead.

**The email agent** does not work that way. Since #2191 it runs as a long-lived HTTP
service (a "sidecar") that the GAIA daemon starts, supervises, and proxies for.
`gaia email` is now a thin client: it finds the daemon, asks it to ensure the sidecar
is up, then streams Server-Sent Events through the daemon's relay
(`src/gaia/daemon/agent_query.py:253`). Nothing about it is a subprocess the TUI can spawn.

So the port is not "wire up a binary". It is "teach the TUI to be a second thin client",
plus the install/settings/autonomy surface the user asked for.

### Two landmines found during review

These are the reason the design below picks the transport it picks.

**(a) The subprocess path silently auto-approves destructive actions.**
`OutputHandler.confirm_tool_execution` returns `True` unconditionally
(`src/gaia/agents/base/console.py:219-225`), and `AgentConsole` — what the agent builds
when driven from a CLI — never overrides it. The email agent gates nine tools behind
confirmation (`send_draft`, `send_now`, `schedule_send`, `forward_message`,
`permanent_delete`, `accept_invite`, `decline_invite`, `create_event_from_email`,
`quarantine_phishing_message` — `agent.py:296`), and on that path **every one of them
executes with no prompt and no event**. If the TUI reuses `subprocess.go`, it ships an
email client that can send mail on the model's say-so. Non-negotiable: do not use that path.

**(b) The canonical SSE endpoint cannot complete a gated action.**
`/v1/email/query` implements the frozen seven-event contract but deliberately has no
resume: on `needs_confirmation` it emits the event, emits a refusal, and kills the run
(`query_routes.py:374-381`). The stateful `/v1/email/agent/query` *can* — it blocks the
agent and waits for a separate `POST /v1/email/agent/confirm-tool` (60s timeout, denies
on expiry, `src/gaia/ui/sse_handler.py:829-902`). Approvals therefore require either the
stateful surface or a contract change to `/query`.

**Decision (D1):** use the canonical `/v1/email/query` relay for normal turns — it is the
frozen contract, it is what `gaia email` uses, and it keeps the TUI provider-agnostic —
and add resume to it (Phase 3) rather than binding the TUI to the email-specific stateful
surface. Fallback if resume slips: use `/v1/email/agent/*` for email only, behind the
same Go interface, and swap later.

---

## 2. What has to be built, by phase

### Phase 1 — Give the TUI an HTTP/SSE transport (foundation)

Everything else depends on this. `client.AgentClient` is already transport-agnostic
(`Send(ctx, query) (<-chan interface{}, error)`); only one line in
`root/model.go:130` hardcodes the subprocess implementation.

1. **Daemon discovery + auth in Go.** Read `~/.gaia/host/instance.json`
   (`{pid, port, token, api_version}`, mode 0600, `GAIA_DAEMON_HOME` override). Trust it
   only after verifying the pid is alive *and* `GET /daemon/v1/status` returns
   `service == "gaia-daemon"` with a matching pid — a recycled port after a crash is a
   real case (`src/gaia/daemon/instance.py:135-170`). Enforce the version gate:
   MAJOR == 1, MINOR >= 1. **The token rotates on every daemon restart** — re-read the
   file on any 401, never cache it for the session.
2. **Start-or-attach.** If no live daemon: take `flock` on `~/.gaia/host/instance.lock`,
   re-check, spawn `gaia daemon start` detached, poll for registration (30s cap).
   Mirrors `client.py:80-158`.
3. **SSE client** (`tui/internal/client/sse.go`) implementing `AgentClient`.
   `POST /v1/email/query` with `Authorization: Bearer <daemon token>`,
   `Accept: text/event-stream`, body `{query, run_id (uuid4), context, model?, max_steps?}`.
   Parse the seven canonical events: `status | token | tool_call | tool_result |
   needs_confirmation | final | error`. Exactly one terminal `final|error`; a stream that
   ends without one is a failure, not a success. On cancel, `POST /v1/email/query/{run_id}/cancel`.
   Reference implementation to mirror line-for-line: `src/gaia/daemon/agent_query.py`.
4. **Event vocabulary.** The TUI's current 13 types (`tui/internal/event/types.go`) are
   the *old in-process* vocabulary, not the canonical seven. Add the canonical set,
   including `ToolResultEvent.Render string` and `Data json.RawMessage` (see Phase 5).
   Keep the legacy types for the bash agent.
5. **Transport discriminator on the catalog entry** (`Transport: subprocess | daemon`) and
   a branch at `root/model.go:launchAgent`.
6. **Host owns the transcript.** The sidecar is stateless on `/query`; the TUI must
   accumulate `[{role, content}]` and push it as `context` each turn, appending a turn
   only when it terminated in `final` (so a failed turn doesn't poison the next).

*Known sharp edges to fix while here:* `strings.Fields` command splitting breaks on paths
with spaces; cancellation doesn't actually stop the child; unparsed events are silently
dropped rather than surfaced.

**Size:** ~700-900 lines of Go + tests. The single biggest chunk of the port.

---

### Phase 2 — Install / run / uninstall

**The gap:** there is no install or uninstall path anywhere except the web UI's FastAPI
server (`src/gaia/ui/routers/hub.py:166,232`, port 4200). The daemon has no install API.
There is no `gaia hub install` CLI. The lazy binary fetch in
`src/gaia/daemon/sidecars/fetch.py` is **dead** — the committed
`hub/agents/npm/agent-email/binaries.lock.json` has `PENDING-1648-replace-with-real-sha256`
for all four platforms, and the file isn't shipped in the wheel at all.

**Decision (D2):** add install/uninstall to the **daemon**, not to the TUI. Three clients
(TUI, CLI, web UI) then share one implementation, one lock, one integrity check.
`gaia.hub.installer` already exists and is daemon-safe.

1. **New daemon routes** (`src/gaia/daemon/sidecars/routes.py`):
   - `GET /daemon/v1/catalog` — proxy + cache `https://hub.amd-gaia.ai/index.json`
     (public, unauthenticated, currently lists exactly one agent: email 0.5.0).
   - `POST /daemon/v1/agents/{id}/install {version?}` → 202, plus
     `GET .../install-status` for progress. Must stop a running sidecar first — the
     install directory *is* the binary cache (mirror `hub.py:58-81`, which aborts with
     500 if the pid survives).
   - `DELETE /daemon/v1/agents/{id}` → stop, verify the pid is gone, then
     `rmtree ~/.gaia/agents/{id}`.
   - Reuse `installer._install_slot` so two clients can't install the same agent at once.
2. **Wire the existing `gaia hub` CLI** to the same routes (there is no install CLI today
   — this is a ~50-line handler and it unblocks scripted setup independently of the TUI).
3. **TUI hub screen:** `i` = install (progress bar), `d` = uninstall (reuse the existing
   `ConfirmModel`), `Enter` = run. Catalog comes from `/daemon/v1/catalog` instead of
   `seedAgents()`. Installed state comes from `~/.gaia/agents/*/.installed` sentinels.
   Show download size (the catalog carries `download_size_bytes`).
4. **TUI CLI switches** (`tui/internal/cli/`):
   ```
   gaia tui                          # hub (default)
   gaia tui run email [--query "..."]
   gaia tui install email [--version X]
   gaia tui uninstall email
   gaia tui list [--installed]
   gaia tui status
   ```
   `run --query` must be a genuine non-interactive one-shot (print to stdout, exit code
   0/1) — today `chat --query` still opens the alt-screen, which makes it useless for
   scripts and CI.
5. **Housekeeping:** flip `interfaces.tui: false → true` in
   `hub/agents/python/email/gaia-agent.yaml:44`. `catalog_test.go` and `smoke_test.go`
   both hardcode `installed == 1` — they will fail the moment email becomes installable.
   That's the canary; update both.

**Blocker to resolve first:** `builtin_specs()` (`sidecars/spec.py:68`) is a static dict
containing only `email`, with `token_env_var` / `service_id` / `expected_api_major`
hardcoded per agent. "Install anything from the catalog and run it" needs those synthesized
from the installed `gaia-agent.yaml`. For an email-only v1 this can be deferred, but the
hub screen will happily install agents the daemon then refuses to start — so either
synthesize, or filter the catalog to what `builtin_specs()` knows.

**Size:** ~400 lines Python (daemon routes) + ~500 lines Go + tests.

---

### Phase 3 — Interactive and autonomous by default

**What "autonomous" can honestly mean today.** There is no autonomy setting anywhere in
the codebase. Grep finds only doc comments pointing at unimplemented issues.
`docs/spec/autonomous-agent-mode.md:31-38` specifies `manual | goal_driven | autonomous`
with autonomous as the default — 0% implemented. But the *ingredients* exist:

- The job scheduler is already on by default (`config.py:148`) and fires scheduled sends
  and snooze-restores unattended.
- The daily briefing exists, off by default, behind three env vars, requiring a restart.
- Reversible actions (archive, label, mark-read, trash) are **already** unconfirmed, with
  a 30-second undo window.
- The nine irreversible actions already prompt.

**Decision (D3):** ship a three-level `autonomy` setting whose default is "autonomous",
defined as: *run everything reversible without asking; always ask before anything
irreversible or externally visible.* This is honest to the spec's own G9 ("no destructive
action without live user approval regardless of mode") and needs no goal engine.

| Level | Behaviour |
|---|---|
| `manual` | Ask before every tool that writes anything. |
| `assisted` | Ask before irreversible actions; run reversible ones. |
| `autonomous` (default) | Same approvals, **plus** background work on: proactive inbox scan on a timer, daily briefing on, follow-up check on a timer. |

1. **Approval UI.** New canonical event handling for `needs_confirmation` → a modal built
   on the existing `components/confirm.go`, showing action + summary, with
   Approve / Deny / Always-allow-this-action-this-session. Blocks input, honours the 60s
   server timeout with a visible countdown.
2. **Resume on `/v1/email/query`** (the contract change flagged in D1): keep the run alive
   on `needs_confirmation` and accept `POST /v1/email/query/{run_id}/confirm {approved}`.
   This is a contract MINOR and needs the spec doc, `openapi.email.json`, `SPEC.md`,
   `README.md`, `SKILL.md`, and `CHANGELOG.md` updated together — see the doc-sync rule in
   CLAUDE.md. Alternative if this is too big: point the TUI at `/v1/email/agent/query` +
   `/confirm-tool`, which already works, and migrate later.
3. **Autonomous background work:** the briefing currently needs env vars + a sidecar
   restart. Either make it settable at runtime, or have the TUI run its own timer against
   the read-only, non-gated `pre_scan_inbox` and `check_followups`. The TUI-timer version
   is materially cheaper and gets 80% of the perceived value.
4. **Fix the auto-approve hole** (landmine (a)) regardless of which path ships — either
   make `AgentConsole` deny-by-default when non-interactive, or make the base handler
   refuse rather than return `True`. This is a security fix that stands on its own.

**Size:** ~300 lines Go (approval UI) + ~250 Python (resume) + docs.

---

### Phase 4 — Settings

**The gap is severe.** Of roughly 22 settings worth exposing, exactly **two** have an HTTP
API: the memory toggle (`POST /v1/email/agent/memory`) and connectors
(`GET/POST/DELETE /v1/email/connectors`). `EmailAgentConfig` is a plain in-memory
dataclass — no file, no env, no persistence, constructed fresh every time. Session
creation ignores config entirely (`agent_routes.py:272-274` passes zero kwargs).

1. **A config file that does not exist yet:** `~/.gaia/agents/email/config.json`, read at
   agent construction, with `GET/PUT /v1/email/config` to read and write it. Changes that
   need a restart must say so in the response.
2. **Settings screen** (`tui/internal/ui/settings/`), reachable with `s` from the hub and
   `/settings` from chat:
   - Autonomy level (Phase 3)
   - Connected accounts — status, connect, disconnect (the only part with a working API today)
   - Model + context size
   - Memory on/off (works today)
   - Daily briefing on/off + time
   - Undo window, follow-up window
   - Priority / low-priority senders (currently only reachable by asking the agent in
     natural language)
3. **Connector flow, v1:** `GET /v1/email/connectors` already returns
   `{provider, connected, account_email, scopes, can_send}` per provider. Connections live
   in a machine-global keyring shared with the web UI and `gaia connectors`, so most users
   will already show `connected: true` and need nothing. When not connected: POST configure,
   the sidecar opens the system browser, and the TUI shows the URL as a copy-paste fallback
   plus a 120-second spinner. There is no device-code flow and no headless path.
   Google also requires a client id **and** secret typed into a terminal — genuinely poor
   UX, which is exactly why Phase 6 exists.

**Size:** ~350 lines Python (config file + endpoints) + ~600 Go (settings screen).

---

### Phase 5 — UX wins that are cheap and land hard

Ordered by value-per-hour. All of these are pure TUI work against APIs that already exist.

1. **Preflight / readiness screen — the single best item in this scope.** Four calls,
   four checkmark rows, each with a copyable fix:
   `GET /daemon/v1/status` (daemon up) · `GET /daemon/v1/agents` (sidecar running) ·
   `GET /v1/email/init` (Lemonade reachable, version >= 10.2.0, model downloaded, live ctx
   size) · `GET /v1/email/connectors` (mailbox connected, agent granted).
   `/v1/email/init` already returns a structured readiness report *with an actionable
   `hint` string*, 200 when ready and 503 when not, same body either way. Today the user
   discovers each of these preconditions by hitting a wall one at a time.
2. **The inbox pre-scan card.** The agent already emits `render: "email_pre_scan"` on
   `tool_result` (`sse_translation.py:54`) carrying urgent / actionable / suggested-archive
   lists with sender, subject and a reason, plus pre-cap totals and which preferences were
   applied. Crucially the tool's docstring **tells the model not to describe the results in
   prose** because a card is expected — so a client that ignores `render` gets one terse
   sentence and nothing else. Rendering this turns the flagship interaction from a
   shrug into the product. Multi-mailbox scans also return `mailbox_errors[]`, which is a
   free "this account's grant is broken" banner.
3. **Generic render primitives.** The contract already defines `table`, `key_value`,
   `list`, `diff` (`docs/spec/agent-ui-query-sse-contract.md:238-274`) that any agent may
   emit. Implement the four and every future agent card renders with no TUI change.
4. **Error remedy ladder.** `playground_html.py:358-370` already encodes cause → command →
   hint, specific-before-generic. Port it verbatim; it covers Lemonade down, model missing,
   no mailbox, missing grant, revoked token, ambiguous provider, version mismatch.
5. **Briefing splash on launch** — `GET /v1/email/briefing` reuses the pre-scan renderer;
   404 shows the enable hint. Free once #2 exists.
6. **Model provisioning from inside the TUI** — `POST /v1/email/init` streams progress
   lines while pulling the model; the final line's `✓`/`✗` is authoritative. Turns
   "go run `gaia init` and come back" into a progress bar.
7. **Triage panel** — port `renderTriage` (`playground_html.py:433-448`) 1:1: category pill,
   spam/phishing badges, summary, action-item checklist.
8. **Inbox table + calendar list** — `POST /v1/email/search` returns paged rows with a
   cursor; `GET /v1/email/calendar/events` returns event rows. Both already typed.

**Not free (needs a backend line each):** rich cards for follow-ups, scheduled jobs, and
sender profiles. For any tool without a `render` entry the payload degrades to
`{summary, success, latency_ms}` — there is nothing to draw. Adding one is two lines
(the tool returns `{"ok", "data": {"kind": ...}}` and both copies of `_RENDER_TOOL_TO_LANG`
gain an entry) but it is a contract MINOR. Emitting the generic `table` key costs no
frontend work at all and is the cheapest path.

**Also missing entirely:** there is no REST route for the persisted action-items/tasks
table, so a "tasks" pane needs backend work.

---

### Phase 5b — Retire `gaia email` from the legacy Python CLI

Once the TUI can install, run, and configure the email agent, the `gaia email` subcommand
is a second front door to the same sidecar with none of the UI. Remove it.

**This is a decommission, not a rewrite.** Only the `email` subcommand goes. The rest of
the legacy Python CLI stays exactly as it is — it will be retired separately, later.

- Remove the `email` subparser (`src/gaia/cli.py:1772-1829`), `handle_email_command`
  (`:5012-5095`), and `_email_interactive` (`:5098-5134`).
- **`src/gaia/daemon/agent_query.py` becomes dead code — decide deliberately.** Its
  `run_query` / `ConsoleRenderer` have exactly two callers, both inside the email handler
  being deleted, so removing the subcommand orphans all 346 lines. It is *not* shared
  infrastructure. Two defensible options: keep it as the reference implementation the Go
  transport mirrors and the thin-client the next agent will reuse (then say so in a module
  docstring, so the next reader doesn't delete it as unused), or delete it with the
  subcommand and let the Go client stand alone. Pick one; do not leave it unexplained.
- `--spec` needs a home before the removal lands — it is the only email surface that
  needs no daemon and no LLM. Either move it under `gaia hub` or expose it in the TUI.
- Delete the now-dead tests, and update `docs/reference/cli.mdx`, `docs/guides/email.mdx`,
  and every doc that shows a `gaia email ...` invocation. The doc-sync rule in `CLAUDE.md`
  applies: grep the old command across all of them, including the email package's own
  `README.md` / `SPEC.md` / `SKILL.md` / `CHANGELOG.md`.
- Print a clear pointer to the replacement rather than a bare "unknown command".

**Sequencing is the whole risk here.** This must not merge until Phases 1-3 are verified
working end to end, or we remove the only working entry point before the replacement
exists. Prepare it early, land it last.

---

### Phase 6 (v2) — Agent-led connector onboarding · **filed as #2469, do not build now**

Today, connecting a mailbox means the user finds Settings, obtains a Google OAuth client
id and secret, and pastes both into a terminal. That is the worst moment in the product.

The v2 target: the agent notices it has no mailbox and handles it conversationally —
"I need access to your Gmail to do that. Want me to walk you through it?" — driving the
flow itself, asking only for what it genuinely can't obtain, opening the browser, and
confirming when it lands. Same pattern for a revoked token or a missing scope mid-task:
the agent should recover in-conversation rather than returning a 403 for the user to decode.

Prerequisites this needs and doesn't have: a way for the agent to request structured input
mid-run and get an answer back (Phase 3's resume mechanism is the same primitive), a
first-party OAuth client so users don't supply their own credentials, and probably a device-code
flow so nothing depends on a local browser. Worth an issue; not worth blocking v1.

---

## 3. Order and rough size

| Phase | What | Blocks | Rough size |
|---|---|---|---|
| 1 | HTTP/SSE transport + daemon discovery | everything | L |
| 2 | Install / run / uninstall + CLI switches | — | L |
| 5.1 | Preflight screen | — (do early, it's cheap) | S |
| 5.2 | Pre-scan card + generic primitives | 1 | M |
| 3 | Approvals + autonomy default | 1 | M |
| 4 | Config file + settings screen | 1 | L |
| 5.3-8 | Remaining UX polish | 1, 5.2 | M |
| 6 | Agent-led onboarding | 3 | issue only |

Phases 1 and 2 are independent of each other and can run in parallel (one Go-heavy, one
Python-heavy). Phase 5.1 should jump the queue — it is small and it is the difference
between "it didn't work" and "here's what to fix".

## 3b. Design review outcome — amendments to this plan

The user-journey design (`docs/plans/tui-user-journey.md`) reviewed this plan and pushed back
on ten points. Accepted amendments, in order of how much they change the build:

- **Preflight moves into Phase 1 as its exit test.** It was scoped here as Phase 5.1 polish
  that "should jump the queue". That undersold it: without the readiness gate, every failure
  of the new transport — daemon down, token rotated, version gate failed, model missing —
  reaches the user as a raw HTTP status. Phase 1 isn't done when the stream parses; it's done
  when a stream that *can't start* explains itself.
- **Drop "always allow" from the approval UI.** The nine gated tools are gated precisely
  because they're irreversible, and "this session" has no boundary a user can hold in a TUI
  that stays open for days. Attack approval *cost* — two keystrokes, no scrolling — not
  approval count.
- **Replace the three-level autonomy enum with two plain controls.** `manual | assisted |
  autonomous` is implementer vocabulary; approvals are identical across the top two levels by
  design, so the only real axis is whether background jobs run. Ship fixed "always ask before
  irreversible things" plus a background-work checklist. Same code, nothing to teach, and it
  doesn't promise a goal engine that doesn't exist.
- **No background timers inside the TUI.** The TUI is closed most of the time, so a timer in
  it means "autonomous while you watch" — the one case where just asking is easier. Scan on
  launch instead: simpler, and it's what produces the day-5 screen.
- **`builtin_specs()` — filter, explicitly.** Label unrunnable catalog entries as such rather
  than offering them as Available. Zero cost, closes the dead end. Synthesis becomes real work
  when the second installable agent arrives.
- **Three render primitives, not four.** `image` is base64 raster and can't render in a
  terminal; `diff` has no producer. Build `table`, `key_value`, `list` plus the fallback.
- **Settings screen shrinks to what persists** (accounts, memory, app-local prefs), with the
  budget moved to the config file that makes the rest real. Filed as #2470; scheduled-jobs
  API as #2471.

Three further pre-existing bugs found on the first-run path, added to §5: the hub opens on an
empty `Installed` tab on a fresh machine; its 26-row chrome budget overflows an 80x24
terminal; and connection state is signalled by colour alone. Also `backspace` currently
triggers uninstall alongside `d`/`delete` — harmless today, destructive once uninstall is
real — and chat's `/init` prints a message and does nothing.

## 4. Open decisions

- **D1** — `/query` + resume, or `/agent/query` + `confirm-tool`? Resume is the better
  long-term contract but is a spec change touching six documents.
- **D2** — install in the daemon (shared by three clients) or in the TUI (faster, forks
  the logic). Recommend the daemon.
- **D4** — does the TUI ship inside the `amd-gaia` wheel, or as its own binary? Affects how
  `gaia tui` is even invoked, and whether Go tests run in CI (they don't today).
- **D5** — email-only v1, or generic-agent v1? `builtin_specs()` currently knows only email.

## 5. Pre-existing bugs found during review (worth filing regardless)

1. `AgentConsole` auto-approves every gated tool on the CLI path — send, forward,
   permanent delete, RSVP (`src/gaia/agents/base/console.py:219`). **Security.**
2. `binaries.lock.json` ships placeholder SHAs, so daemon lazy-fetch always fails; and the
   file isn't in the wheel, so pip installs can't reach it either.
3. `gaia email -q X -i` silently discards `-q` (`cli.py:5083`).
4. `--trace`, `--stats`, `--stream`, `--list-tools`, `--max-steps` are parsed and ignored on
   `gaia email`. `--use-claude` / `--use-chatgpt` are accepted and silently ignored — the
   local-only guarantee rests on non-consumption, not rejection.
5. `gaia email "query"` (positional) doesn't work despite the handler docstring saying it does.
6. `--spec` with the package missing raises a raw traceback instead of a clean error.
7. `hub/agents/python/email/gaia_agent_email/cli.py` is dead code (117 lines, no entry point,
   no importer) and its docstring describes a dispatch path that no longer exists.
8. `TestHubTabSwitching` / `TestHubSearch` send the three runes `t`,`a`,`b` instead of the Tab
   key and only assert "didn't panic".
9. `findBinaryInRepo` only looks in `cpp/build/*` for `.exe` — can never find a Python or
   frozen agent on macOS/Linux.
