# Agent UI v2 — Thin-Host Implementation Breakdown

> **Date:** 2026-07-01 · **Grounded at:** `main` @ `080e7259` (post-#1910 email cutover)
> · **Refreshed 2026-07-13** for merged email work: contract **2.3**, per-session sidecar
> auth (#1706 closed by #1980), two in-sidecar clocks (#1918/#1919) + durable stores
> (#1917/#1919), and the merged `gaia schedule` CLI (#1371).
> **Design:** [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md) §0.0–§0.42 +
> [`agent-ui.mdx`](agent-ui.mdx) "Architecture (v2 — sidecar-first)" (merged in #1913).
>
> This document decomposes the v2 design into sequenced, independently-shippable
> issues. It is a **planning artifact** — the issues below are *proposals*, not yet
> filed. Every exists/partial/net-new claim was verified against the code at the
> commit above.

## Corrections to the design doc (design vs. merged code)

Verified deviations an implementer must know before trusting the design text:

1. **`gaia api` still runs email in-process.** §0.33 is right that
   `src/gaia/api/openai_server.py:135-147` mounts `gaia_agent_email.api_routes`
   (plus the `/v1/email/agent/*` surface) in-process — #1910 removed that pattern
   from the **UI backend only**. The API
   server was explicitly out of #1910's scope, so today the repo ships *both*
   models simultaneously: sidecar-backed `/v1/email/*` on the UI, in-process
   `/v1/email/*` on `gaia api`. Tracked as V2-17 below.
2. **Multiple clocks now exist and must be reconciled.** `gaia schedule` + cron
   dispatch **merged** (#1371), so there is now a CLI scheduler alongside the UI
   backend's (`src/gaia/ui/scheduler.py`, 1231 LoC + `routers/schedules.py`, 239
   LoC — landed via PR #1566; tracking issue #550). On top of those, the email
   agent now runs **two in-sidecar clocks** (§0.22): `BriefingScheduler`
   (`briefing.py`, #1918) and `EmailJobScheduler` (`scheduler.py`, #1919). §0.22's
   "relocate the clock" is therefore a multi-clock reconciliation, all folding into
   V2-15 — see its widened scope.
3. **The schema-2.3 contract still has no `/query` and no `/shutdown`; per-session
   auth already landed.** `hub/agents/email/python/openapi.email.json` (v2.3)
   declares 16 deterministic `/v1/email/*` routes and still declares no
   `securitySchemes` — but the sidecar now enforces per-session bearer auth at
   runtime (#1980, closing #1706). The agent loop (`/query`, targeting contract
   **2.4** — #2016) and graceful shutdown remain **additive contract surface**
   (the manager reaps via process-tree signals today). Because auth already merged,
   V2-3 no longer builds it — it narrows to secure secret *delivery* + manager
   generalization.
4. **`EmailProxyAgent` (merged in #1910) is design-superseded but load-bearing.**
   `src/gaia/ui/email_sidecar/proxy_agent.py` (330 LoC) is the *working* email
   chat path today. It is retired by V2-10 only after `/query` + the render map
   exist — not deleted up front.
5. **Sibling plan docs still lack superseded banners.** #1913's own §0.27 names
   `security-model.mdx`, `email-sidecar-agent-ui-implementation.md`,
   `connectors.mdx`, and `autonomy-engine.mdx` as now-misleading; the merge added
   a banner to `email-sidecar-agent-ui.md` only. V2-4 closes this (also flagged
   in #1913's review).

Existing issues to link (not duplicate): #1980 (sidecar per-session auth, MERGED —
closed #1706), #1653 (UI consumes the npm sidecar — dogfood), #1896 (email-in-UI
epic), #1717 (hub catalog schema v2).

---

## 1. Delta analysis — v2 building blocks vs. the code today

| # | v2 building block (design §) | Status | What exists today (verified) | Files touched in v2 |
|---|---|---|---|---|
| 1 | **Headless custody daemon** + `/host/v1/*` (§0.0, §0.25, §0.31) | **Net-new** | No daemon anywhere. The closest thing is the UI backend process (`src/gaia/ui/server.py`, 909 LoC), which dies with the browser session and owns everything in-process. No `instance.json`, no client-auth token, no `gaia daemon` CLI. | new `src/gaia/daemon/` (or `src/gaia/host/`); `src/gaia/cli.py` |
| 2 | **Sidecar supervisor** — `AgentSidecarManager` registry (§0.3, §0.13, §0.14) | **Partial** | `EmailSidecarManager` (`src/gaia/ui/email_sidecar/manager.py`, 498 LoC) does spawn/health/`/version`-gate/tree-kill/singleton for **one hard-coded agent** (`_SERVICE_ID = "gaia-agent-email"`, `_EXPECTED_API_MAJOR = "2"`). Verified fetch (`fetch.py`, 147 LoC, SHA-256, atomic rename) and lock/platform loading (`platform.py`, 131 LoC) are agent-agnostic in shape, email-pinned in constants. No registry, no idle reaper, no live-cap. | `email_sidecar/manager.py` → generalized + relocated into the daemon; new registry module |
| 3 | **`/query` SSE contract** — fixed event vocabulary (§0.1, §0.2) | **Partial** (seam exists; contract + endpoint net-new) | The loop→SSE seam exists: `src/gaia/ui/sse_handler.py` (1307 LoC) turns agent-loop output into typed queue events — but in its **own vocabulary** (`status`/`step`/`thinking`/`plan`/`tool_start`/`tool_args`/`tool_result`/`tool_end`/`chunk`/`answer`/`permission_request`/…), not §0.2's (`status`/`token`/`tool_call`/`tool_result`/`needs_confirmation`/`final`/`error`). The sidecar has **no `/query` endpoint** (contract 2.3 is fixed-function only). | `hub/agents/email/python/` (new `/query` route + translation layer); a spec doc freezing the mapping |
| 4 | **Render→component frontend map** (§0.2, §0.15, §0.35.6) | **Partial** | Cards render by **fence-parsing**: `MessageBubble.tsx` (818 LoC) `STRUCTURED_PAYLOAD_LANGS`/`promoteStructuredPayloads` handle exactly **one** card type (`email_pre_scan`); the backend injects the fence via `_capture_render_payload` in `sse_handler.py:475-534`. No `render`-keyed map, no generic primitives (table/key-value/list/image/diff), no unsupported-card fallback. | `src/gaia/apps/webui/src/components/MessageBubble.tsx`, new card-registry + primitives components |
| 5 | **Dispatch / multi-agent orchestration** (§0.18, §0.32) | **Net-new** (v1 picker ≈ partial) | Per-session `agent_type` dispatch exists in-process (`_chat_helpers.py`, 2651 LoC, builds the agent per session; `agent_type == "email"` → `EmailProxyAgent`). No agent picker UI concept beyond that, no orchestrator, no `/host/v1/agents/{id}/invoke`, no taint. | webui session UI; daemon proxy router; (orchestrator = later, own agent package) |
| 6 | **Scheduler clock in the daemon** (§0.22) | **Partial** | Several clocks exist, none in a daemon: the UI backend's `src/gaia/ui/scheduler.py` (1231 LoC, asyncio, NL parsing, DB-persisted) + `routers/schedules.py` (239) + `routers/goals.py` (283); the merged `gaia schedule`/cron CLI (#1371); and the email sidecar's own `BriefingScheduler`/`EmailJobScheduler` (#1918/#1919). Each dies with its owning process (see correction 2). | `ui/scheduler.py` + the in-sidecar clocks → reconciled into the daemon; `routers/schedules.py`/`goals.py` become thin daemon clients |
| 7 | **Mid-workflow confirmation** (§0.4) | **Partial** | The pause/approve primitive ships **in-process**: `permission_request` events + confirm/deny plumbing (`sse_handler.py`, `routers/chat.py` `/api/chat/confirm-tool`). The email sidecar separately has the single-use confirmation-token gate on `/send`. Nothing crosses a process boundary; no `needs_confirmation` SSE event; model (stateless vs. resume) **unsigned-off**. | email sidecar `/query` loop; daemon relay; webui approve/deny; decision D1 below |
| 8 | **OAuth forward** (§0.6) | **Partial — direction inverted** | The primitive exists pointing **the wrong way**: `POST /v1/connections/{provider}` is host-side **intake** (`src/gaia/ui/routers/connectors.py:94` `forwarded_router` → `src/gaia/connectors/api.py:266` `import_forwarded_connection`, `X-Gaia-UI: 1`-gated, scope-checked, metadata-only response). v2 needs the **sidecar** to expose the intake and the host to forward *out* + own refresh, sending short-lived access tokens. Refresh engine (`get_access_token`) is client-neutral and reusable. | `hub/agents/email/python/` (intake route); `src/gaia/connectors/`; daemon forward-on-spawn logic |
| 9 | **Shared-custody stores** (§0.9, §0.29, §0.37–§0.41) | **Partial** | The stores exist as fat in-process UI routers — LoC verified exact: `memory.py` 1634, `documents.py` 739, `sessions.py` 368, `connectors.py` 977, `files.py` 665, `mcp.py` 349 (SQLite via `database.py`, 1139). No `/host/v1/*` surface, no per-agent scoping, no audit sink, no single-writer enforcement beyond one process. | routers → daemon custody modules behind `/host/v1/*`; `database.py` |
| 10 | **Three auth legs** (§0.11) | **Partial** | Leg 2 (daemon→sidecar) landed: the sidecar enforces per-session bearer auth (#1980, closing #1706), though contract 2.3 still declares no `securitySchemes`. Still open: the manager health-probes a loopback port and UI↔backend has no client token. | manager (secret *delivery* via fd/`0600` file + injection); daemon (client token in `instance.json`) |
| 11 | **Install / model provisioning** (§0.5, §0.28) | **Partial** | Verified binary install exists for email: `binaries.lock.json` + SHA-256 fetch + cache under `~/.gaia/agents/email/`. Frontend hub surfaces exist (`agentHub.ts`, `AgentInstallDialog.tsx`, `AgentHubGrid.tsx`). **No manifest** (the lock is integrity-only — §0.28's capability/policy fields don't exist), **no model provisioning** (install never pulls the agent's LLM — the #1655 cold-start class), no uninstall data policy. 17/18 python hub agents have `gaia-agent.yaml`, but only email has an OpenAPI contract + frozen sidecar entry (`packaging/server.py`). | `hub/agents/*/manifest.json` (new schema); daemon install path; webui hub |
| 12 | **Model-slot broker** (§0.12) | **Net-new** | Only an *in-process* `_downloads_lock` in `lemonade_client.py`. Nothing arbitrates cross-process model loads — the race-evict failure CLAUDE.md documents for concurrent evals would hit production the moment two sidecars run. | new daemon broker module; `lemonade_client.py` callers route through it |
| 13 | **`/query` on `gaia api`** (§0.33) | **Net-new + contradiction** | `src/gaia/api/` (1756 LoC) is a separate FastAPI app: `/v1/chat/completions`, `/v1/models`, `/health` — no `/query`, and it still mounts email **in-process** (correction 1). | `openai_server.py` (add proxy route, drop in-process mount) |

**Reading of the table:** the reusable core is the email sidecar's lifecycle code
(#2), the SSE seam (#3), and the OAuth forward machinery (#8). The load-bearing
net-new items are the daemon (#1), the broker (#12), and the auth legs (#10) —
matching §0.23's feasibility verdict exactly.

---

## 2. Sequenced issue breakdown (proposals — not yet filed)

Ordering follows §0.23 (broker + streaming-proxy early, contract-first) and §0.10
(email is the reference migration; the app stays shippable at every step). Sizes:
S ≤ 2 days, M ≈ 3–5 days, L ≈ 1–2 weeks.

### Phase 0 — Contract first (no architecture change yet)

**V2-1 · `docs(spec): freeze the /query SSE event contract + handler-vocabulary translation map`** — **S**
*Why:* every later issue (sidecar `/query`, relay, frontend map, CLI, `gaia api`)
codes against this wire contract; freezing it first prevents N private dialects.
*Scope:* spec doc defining §0.2's seven event types (JSON schema per type), the
`/query` request body (`{query, run_id, context, model?, provider?, max_steps?}`,
host-minted `run_id`), and the **explicit mapping** from `sse_handler.py`'s
existing vocabulary (`tool_start`→`tool_call`, `chunk`→`token`, `answer`→`final`,
`permission_request`→`needs_confirmation`, …) including which events are dropped
or folded. Contract-versioning rules (§0.15: unknown `type` → visible
"unsupported event", never silent).
*Acceptance:* spec merged; both vocabularies tabulated; email agent's SPEC/openapi
regeneration plan named. *Deps:* none.

**V2-2 · `feat(email): add POST /v1/email/query — the SSE agent loop on the sidecar (#2016)`** — **L**
*Why:* this is the v2 keystone — the sidecar becomes a complete agent product
(reasoning included), not a bag of fixed functions; every front-door then relays
to one loop.
*Scope:* new route in the email agent package running its agent loop with an
SSE output handler + the V2-1 translation layer; per-`run_id` state namespacing
(§0.13); `POST /v1/email/query/{run_id}/cancel`; contract bump to **2.4** (additive
MINOR — #2016; the sidecar is already at 2.3);
openapi/`specification.html`/SPEC/SKILL/CHANGELOG updated **together**
(the #1841 rule). Confirmation events per decision D1.
*Acceptance:* `curl -N` against a dev-mode sidecar streams the §0.2 vocabulary
end-to-end for a triage query; cancel stops tool execution between steps; eval
harness scenario asserting the event *sequence* (§0.17) added to the agent
package. *Deps:* V2-1. *Blocks:* V2-7, V2-8, V2-10, V2-17.

**V2-3 · `feat(email): fd/file secret delivery + manager generalization for sidecar auth`** — **S**
*Why:* per-session bearer auth already merged (#1980, closing #1706), so the
remaining §0.11 gap is *how the secret reaches the sidecar* — a bare env var is the
named threat. This shrank from net-new auth to secret delivery + manager plumbing.
*Scope:* deliver `GAIA_AGENT_LAUNCH_SECRET` via inherited fd or `0600` file (not
bare env — §0.11); `EmailSidecarManager` mints + injects it and adds it to proxy
calls; bare-integrator mode (no secret configured) documented as the integrator's
own responsibility. No new auth-enforcement code — #1980 already checks the bearer.
*Acceptance:* the secret never appears in `/proc/<pid>/environ` or `ps` output;
requests without/with-wrong bearer → 401 with actionable detail (tested as a
boundary, not a mock); all existing proxy tests pass with auth on. *Deps:* none
(parallel with V2-2).

**V2-4 · `docs(plans): superseded-model banners on security-model, connectors, autonomy-engine, email-sidecar-implementation`** — **S** (filed as #2017)
*Why:* #1913's §0.27 names these docs as actively misleading ("a reader following
the wrong one builds the wrong thing"); its own review flagged the banners as the
one unfinished follow-up.
*Scope:* the same ⚠️ banner pattern already on `email-sidecar-agent-ui.md`, one
line each on what specifically changed, pointing at v2 §0. Also fix the
`agent-ui.mdx` "What this replaces" sentence that puts RAG/memory in sidecars
(contradicts §0.9 — flagged in #1913 review). *Deps:* none.

### Phase 1 — The daemon and the vertical slice

**V2-5 · `feat(daemon): headless custody daemon skeleton — single instance, client auth, lifecycle CLI`** — **L**
*Why:* §0.0's load-bearing split: without an always-on custody process, CLI-only
use has no callback target and autonomy has no clock owner. Everything in Phase 2
mounts into this process.
*Scope:* new package (recommend `src/gaia/daemon/`): FastAPI app on a loopback
port; `~/.gaia/host/instance.json` (pid + port + minted client-auth token, `0600`,
temp-then-rename atomic write); stale-lock reclaim (liveness-check pid + probe
port/token before trusting — §0.25); auto-start-or-attach helper for clients;
`gaia daemon status|stop|restart|logs`; versioned client API (`/daemon/v1/…`,
§0.25 skew rule); OS process-manager registration (launchd/systemd-user/Scheduled
Task) as a stretch or fast-follow.
*Acceptance:* two concurrent `gaia daemon`-touching invocations yield one daemon
(second attaches); kill -9 then restart reclaims the lock; requests without the
client token → 401; unit tests for lock atomicity + reclaim. *Deps:* none.
*Blocks:* V2-6..8, V2-11..15.

**V2-6 · `refactor(daemon): generalize EmailSidecarManager → AgentSidecarManager registry in the daemon`** — **M**
*Why:* the supervisor is the daemon's core job (§0.3); today it's email-pinned
and lives in the UI process — the interim shape §0.27 explicitly says is not the
end state.
*Scope:* parametrize service-id / expected-major / lock-path / cache-dir; keyed
registry (one shared instance per agent id); relocate into the daemon; the UI
backend's email router becomes a client of the daemon (or is bypassed once V2-7
lands); concurrent-live-sidecar cap (§0.13); dev-mode source-dir registration for
unpublished agents (§0.16) can ride along or split out. **The idle-timeout reaper
must NOT land before V2-15's clock reconciliation** — the email sidecar now runs
its own `BriefingScheduler`/`EmailJobScheduler` (#1918/#1919) in-process, so reaping
an idle sidecar would silently kill its 8am brief and scheduled sends.
*Acceptance:* email runs supervised by the daemon in both `user` and `dev` modes;
existing `test_email_sidecar_*` suites pass against the generalized class; a
second toy agent (fixture) registers and spawns without touching manager code.
*Deps:* V2-5.
*Status:* landed via #2142 — spec-driven `AgentSidecarManager` + registry + crash-reap ledger in `gaia.daemon.sidecars`, `/daemon/v1/agents` control plane, UI backend cut over to `daemon_client`, per-agent CLI (`agents`/`start-agent`/`stop-agent`); idle reaper deliberately deferred to V2-15.

**V2-7 · `feat(daemon): streaming SSE reverse-proxy (`ANY /v1/<agent>/*`) with cancel + crash semantics`** — **M**
*Why:* §0.23 names this net-new: today's `EmailSidecarProxy` is synchronous
`requests` ending in `resp.json()` — it buffers, so `/query` streaming is
impossible through it.
*Scope:* `httpx.AsyncClient(stream=True)` + `StreamingResponse` passthrough
preserving SSE; client-disconnect → propagate cancel to
`/query/{run_id}/cancel`; sidecar crash mid-stream → synthetic terminal `error`
event (§0.13); per-spawn secret injection (V2-3); keep the buffered path for
fixed-function routes (it's fine). Includes the **freeze spike**: prove SSE
through the PyInstaller binary before building on it (§0.23 item 2).
*Acceptance:* deterministic tests against a fake sidecar assert no buffering,
cancel-on-disconnect, and the synthetic `error` event; golden-path curl through
daemon → frozen binary streams. *Deps:* V2-5 (V2-2 for the real-email path).

**V2-8 · `feat(cli): gaia email attaches to the daemon (thin client)`** — **M**
*Why:* proves "one contract, many front-doors" with the cheapest client (no
frontend work), and closes the §0.0 hole where CLI-spawned work has no custody
home. This is the tail of the first increment (§3).
*Scope:* `gaia email "<query>"` auto-starts/attaches to the daemon and drives
`POST /v1/email/query` through the proxy, rendering the event stream in the
console; behavior parity with the current in-process path for the golden cases;
today's delegation to the wheel's in-process CLI (`gaia_agent_email.cli:main`,
`src/gaia/cli.py:4786` — note: §0.23 says `cli.py` builds `EmailTriageAgent`
directly; it actually delegates to the wheel, but the run is in-process either
way) is retired for the query path.
*Acceptance:* works with the web UI closed; UI and CLI hitting the same agent
share one sidecar (one pid); output parity spot-checked; `gaia email` still fails
loud with actionable errors when Lemonade is down. *Deps:* V2-2, V2-5–V2-7.

**V2-9 · `feat(webui): render→component map from tool_result events + generic render primitives`** — **M**
*Why:* fence-parsing (`STRUCTURED_PAYLOAD_LANGS`) is the hack v2 retires; the
§0.35.6 primitives are what make "the thin UI renders *any* agent" true instead
of a per-agent component treadmill.
*Scope:* card registry keyed on `render`; port `EmailPreScanCard`; generic
primitives (table / key-value / list / image / diff); explicit "unsupported card"
fallback for unknown `render` types (§0.15); delete the fence-injection path in
`sse_handler.py` + `promoteStructuredPayloads` only at V2-10 cutover.
*Acceptance:* `email_pre_scan` renders from a `tool_result` event; an unknown
`render` shows the fallback, never nothing; primitives storybook/screenshot
evidence. *Deps:* V2-1.

**V2-10 · `feat(ui): route email chat through sidecar /query — retire EmailProxyAgent`** — **M**
*Why:* completes the reference migration (§0.10 step 1): the UI stops running a
reduced in-process tool loop and relays the sidecar's full loop, removing #1910's
tool-surface reduction.
*Scope:* email sessions stream `/query` events through the daemon relay into the
new card pipeline; confirmation approve/deny wired per D1; delete
`proxy_agent.py` (330 LoC) + its `_chat_helpers.py` dispatch; email eval category
re-run vs. baseline (LLM-affecting — CLAUDE.md rule).
*Acceptance:* golden path (triage → pre-scan card → destructive step →
confirmation → final) passes on hardware per `gaia-testing`; eval scorecard
compared to committed baseline. *Deps:* V2-2, V2-7, V2-9.

### Phase 2 — Custody, broker, clock (the daemon earns its name)

**V2-11 · `feat(llm): host-owned model-slot broker (serialize loads, interactive-priority queueing)`** — **L**
*Why:* §0.23's critical path — Lemonade is single-tenant per slot; two active
sidecars without a broker race-evict exactly like concurrent evals do. Gates any
multi-agent story.
*Scope:* daemon module owning load serialization + `POST /host/v1/models/lease`;
interactive > background priority **queueing** (defer preemption — §0.35.5);
hot-model affinity hint; `switching model…` status surfaced. Sidecars/host-RAG
route loads through it.
*Acceptance:* concurrency test proving two agents requesting different models
serialize (no race-evict) and foreground jumps the queue; email sidecar +
host-side embedder coexist without the #1030 ctx-cap regression. *Deps:* V2-5.

**V2-12 · `feat(daemon): /host/v1/* callback API v1 (memory, rag, sessions, audit) with per-agent scoping`** — **L**
*Why:* §0.31 is the reverse contract the whole custody model rides on; without
per-agent scoping it is a single-reader exfiltration surface (§0.11's decisive
point 🔒).
*Scope:* the §0.31 route table (rag/query, memory GET/POST, sessions/{id},
audit append — plain append-only for v1, hash-chain deferred per §0.35.5);
secret→agent-id binding at mint; every response scoped to the caller; typed loud
errors (403/409/429/503); own contract MAJOR. Backed initially by the existing
SQLite stores (WAL + busy_timeout — §0.29).
*Acceptance:* boundary tests: agent A cannot read agent B's memory/sessions;
missing/wrong secret → 403; audit rows survive sidecar uninstall. *Deps:* V2-5,
V2-3. *Blocked by decision D2* for what moves vs. stays.

**V2-13 · `feat(daemon): one-time versioned data migration of existing ~/.gaia state`** — **M**
*Why:* §0.10 step 0 — without it, upgraders lose past chats or memory leaks
cross-agent.
*Scope:* stamp on-disk schema version; existing sessions → host index with
default agent tag; existing memory → host user-scope; idempotent.
*Acceptance:* migration runs twice from a real pre-v2 `~/.gaia` fixture → second
run is a no-op; cold-state test per CLAUDE.md. *Deps:* V2-12.

**V2-14 · `feat(connectors): OAuth forward-OUT — sidecar /v1/connections intake + daemon-owned refresh`** — **M**
*Why:* §0.6's role inversion: N sidecars each holding a refresh token would
rotate each other out; the daemon must stay the single writer and forward
short-lived access tokens.
*Scope:* intake route on the email sidecar (reusing the
`import_forwarded_connection` shape); daemon forwards on spawn + re-forwards on
expiry; sidecar never receives the refresh token; uninstall revokes the forward
(§0.20).
*Acceptance:* sidecar operates on a forwarded access token with no keyring/grants
file access; expiry mid-session re-forwards transparently; revocation test.
*Deps:* V2-3, V2-5 (daemon as forwarder).

**V2-15 · `refactor(daemon): reconcile all clocks into the daemon; jobs fire into the owning sidecar`** — **L**
*Why:* §0.22 — a reaped sidecar (V2-6's reaper) can't fire its own 8am brief, and
today's clocks die with the process that owns them, so "always-on" is currently
false. This is now a **multi-clock** reconciliation, not a single relocation.
*Scope:* fold **four clocks** into one daemon-owned scheduler: (1) `ui/scheduler.py`'s
engine, (2) the merged `gaia schedule`/cron CLI (#1371), and (3+4) the email
sidecar's in-process `BriefingScheduler` (#1918) and `EmailJobScheduler` (#1919).
Their durable stores must move to host custody too — the email `task_store` (#1917)
and `schedule_store` (#1919). Schedule metadata becomes daemon custody; fire-time =
spawn owning sidecar (if not resident) + `/query`; `routers/schedules.py`/`goals.py`
become thin daemon clients. Until this lands, V2-6's idle reaper must stay off (a
reaped sidecar can't fire its own clocks).
*Acceptance:* a briefing/scheduled-send fires with both the web UI and the CLI
closed; results and task/schedule state land in host custody; existing
ScheduleManager panel keeps working; the email agent's own scheduler tests still
pass. *Deps:* V2-5, V2-6 (+V2-12 for result/store custody).

### Phase 3 — Fleet-readiness and remaining front-doors

**V2-16 · `feat(hub): agent manifest schema + install-time model provisioning`** — **L**
*Why:* §0.28 — eight sections lean on a manifest that doesn't exist (the lock is
integrity-only), and install that skips model download ships the #1655 cold-start
failure to every new user.
*Scope:* `manifest.json` schema (id, contractMajor, requiredModels, capabilities,
renderTypes, oauthScopes, …) referenced by digest from the lock; install-time
validator that fails loud; install pulls `requiredModels` through the broker with
UI progress; contract-range gate at install (§0.15). Signing/tiers deferred to D3.
*Acceptance:* fresh-profile install of email on a machine without the model →
first `/query` works (cold-state test); out-of-range contract rejected loudly.
*Deps:* V2-11 (broker pull), V2-6.

**V2-17 · `feat(api): expose /v1/<agent>/query on gaia api via the daemon; drop the in-process email mount`** — **M**
*Why:* closes correction 1 — the API server is the last in-process email surface —
and gives programmatic consumers the agentic loop, not only OpenAI-style chat
(§0.33).
*Scope:* proxy route on `openai_server.py` (behind its API-key auth — stricter
than loopback, §0.33); remove the `gaia_agent_email.api_routes` mount;
`/v1/chat/completions` untouched.
*Acceptance:* REST consumer streams `/query` through `gaia api` with an API key;
no `gaia_agent_email` import remains in `src/gaia/api/`. *Deps:* V2-2, V2-5, V2-7.

**V2-18 · `feat(webui): explicit per-session agent picker (v1 dispatch)`** — **S**
*Why:* §0.18 — with N sidecars nothing chooses the `/query` target; the picker is
the zero-LLM-cost v1 answer (the orchestrator is a later agent, not host logic).
*Scope:* session-level agent selection driving the daemon proxy target; matches
the CLI's explicit `gaia <agent>` semantics.
*Acceptance:* two sessions pinned to different agents route to different
sidecars. *Deps:* V2-7, V2-10.

**V2-19 · `test(eval): sidecar eval harness + distributed-seams test suite`** — **M**
*Why:* §0.17 — `/query` is the most LLM-affecting surface v2 adds, and the
daemon/broker/relay/auth seams each have deterministic failure modes unit tests
must pin before third-party agents amplify them.
*Scope:* eval harness driving `/query` over REST asserting event *sequences*
(baselines live in the agent package, runs stay serial); consolidated
deterministic suites for the relay (V2-7), three auth legs (V2-3/5/12), broker
concurrency (V2-11), migration idempotency (V2-13) — this issue tracks the
*harness + gaps*, individual issues ship their own tests.
*Acceptance:* CI job running the seam suite; one on-hardware golden path per
`gaia-testing` (UI → daemon → sidecar → Lemonade). *Deps:* V2-2 (harness), rest
incremental.

**Dependency spine:** V2-1 → V2-2 → {V2-7, V2-8, V2-10, V2-17}; V2-5 → {V2-6,
V2-7, V2-11, V2-12, V2-15}; V2-12 → V2-13. V2-3 and V2-4 have no upstream
dependency and can start immediately; V2-9 needs only the V2-1 spec.

---

## 3. First increment — the smallest end-to-end vertical slice

**Claim to prove:** *a headless daemon can supervise a real sidecar and relay its
agent loop, streamed, to a thin client that isn't the fat UI backend.* That is
the thin-host model in one demo; everything else is widening.

**Composition:** V2-1 + V2-2 (minimal) + V2-3 + V2-5 + V2-6 (minimal) + V2-7 +
V2-8. The thin client is the **CLI**, deliberately — it exercises the §0.0 split
(works with no browser) with zero frontend work; the web UI joins in V2-9/V2-10.

**Implementer spec:**

1. **Sidecar side** (email package): `POST /v1/email/query` returning SSE in the
   V2-1 vocabulary. Minimal scope: `status`/`tool_call`/`tool_result`/`token`/
   `final`/`error` (confirmation events stubbed pending D1 — a destructive step
   returns a `final` refusal telling the user to use the fixed-function route);
   bearer-secret check on every route.
2. **Daemon**: skeleton per V2-5 (instance.json, client token, attach-or-start,
   `gaia daemon status|stop`); `AgentSidecarManager` with email registered
   (constants parametrized, relocation only as far as needed — the UI backend may
   keep its existing router as a second client for now); streaming reverse-proxy
   for `/v1/email/*` with cancel-on-disconnect + synthetic crash `error`.
3. **Client**: `gaia email "<query>"` attaches to the daemon and renders the
   stream. No custody (`/host/v1/*`), no broker, no scheduler, no frontend change
   in this slice.

**Demo script / acceptance:**

```bash
# no daemon running, web UI closed
gaia email "what needs my attention today?"   # auto-starts daemon, spawns sidecar, streams
gaia daemon status                            # shows daemon pid + email sidecar (mode, port, pid)
# second terminal, mid-stream:
gaia email --cancel <run_id>                  # or Ctrl-C → run stops between tool steps
kill -9 <sidecar-pid>                         # mid-query → client receives terminal error event, not a hang
gaia daemon stop                              # tree-kills sidecars, releases instance.json
```

Plus: unauthenticated `curl` to the sidecar port → 401; two concurrent
`gaia email` invocations share one sidecar pid; dev-mode
(`GAIA_EMAIL_AGENT_MODE=dev`) works end-to-end.

**Explicit non-goals of the slice:** custody callbacks, model broker (single
active agent — acceptable), confirmation flow, render map, install/hub, data
migration. Each has its own issue above.

---

## 4. Open decisions needing sign-off (@itomek)

These are design-acknowledged open calls (§0.§ "Open decisions"); the issues
above are sequenced so none blocks Phase 0/1, but D1 blocks V2-10's confirmation
wiring, D2 blocks V2-12 scoping, and D3 gates any third-party agent.

**D1 — §0.4 Confirmation model: stateless stop-and-hand-off vs. stateful resume?**
The design recommends **stateless**: `needs_confirmation` ends the stream; the
host performs the approved deterministic call itself (single-use token), then
issues a fresh `/query` carrying the approved-step state; `run_id` stays purely a
cancellation handle (no `confirm_url`, no server-side continuation store). The
resume alternative keeps the run paused in the sidecar (a `run_id`→continuation
map with TTL), which is friendlier for many-confirmation workflows but makes the
sidecar stateful — fragile across dev-reload/crash/uninstall. **Either way the
approve-what-you-saw invariant is hard:** the continuation must resume the exact
approved step, never re-plan it. → *Question: adopt stateless for v1 (recommended),
promoting to resume only if real workflows prove host-side stitching too clumsy?*

**D2 — §0.9 Memory/RAG custody home: daemon custody vs. dedicated sidecars?**
The design recommends **daemon custody** (single writer across N agents; the
§0.35 review endorses it — dedicated memory/RAG sidecars would reintroduce the
multi-writer problem), refined by §0.37/§0.40: custody is a *pluggable, per-store*
provider, and "daemon custody" is what the Agent UI deployment selects (a
standalone sidecar embeds its own). This is the single decision that most shapes
the migration (V2-12/V2-13 and what remains of `memory.py`/`documents.py`).
→ *Question: sign off daemon custody for the Agent UI deployment, with the
pluggable `CustodyProvider` (embedded/delegated/ephemeral) as the sidecar-side
abstraction?*

**D3 — §0.24 Third-party trust root + containment 🔒**
Three coupled calls that must be settled **before the first third-party agent is
installable** (they gate V2-16's scope; first-party email is unaffected):
(a) **signing** — who holds the key that signs the lock/catalog (SHA-256 is
integrity, not authenticity), plus anti-rollback; (b) **egress containment** —
no-network-by-default with a manifest-declared allowlist, enforced via the
host proxy (v1 recommendation) vs. a per-OS network namespace (deferred), with
the daemon control channel always allowed; (c) **tier-gated, least-privilege
OAuth forwarding** — forwarding a live mailbox token to a Community/Experimental
agent requires explicit scope-naming consent. Security-sensitive — recommend the
specifics move to a private security advisory / maintainer thread rather than a
public issue. → *Question: confirm (a)–(c) as the containment baseline and who
owns the signing root, so V2-16 can scope what v1 install enforces vs. defers?*

---

## 5. What this breakdown deliberately defers

Named so their absence reads as sequencing, not omission: multi-agent
orchestrator + A2A alignment (§0.32, §0.42 — a later first-class agent on the
same contract), the autonomy policy layer (§0.34 — a layer above the contract,
after human-in-the-loop v1), hash-chained audit + network-namespace sandbox +
mid-run broker preemption (§0.35.5 — third-party gate, not v1 build), voice as
host I/O, and the C++/OEM surfaces. None changes the contracts frozen in Phase 0.
