# Agent UI Agent Capabilities Plan

> **Branch:** `kalin/agent-ui`
> **Date:** 2026-03-06
>
> **Two Milestones:**
> - **Milestone A** — Agent UI: Wire Existing SDK Capabilities (#15)
>   *Expose existing GAIA SDK features to the Agent UI. No new SDK code — just wiring,
>   MCP integration, and UI work.*
> - **Milestone B** — GAIA Agent SDK: New Capabilities (TBD)
>   *Enhance the core GAIA Agent SDK with capabilities that don't exist yet:
>   guardrails framework, screenshot capture, computer use, voice, etc.*

---

## Milestone Scope Summary

### Milestone A — Agent UI: Wire Existing SDK Capabilities
**Goal:** Make ChatAgent as capable as possible using what the SDK already has.

| Category | What to Do | New SDK Code? |
|----------|-----------|---------------|
| File I/O | Add `FileIOToolsMixin` to ChatAgent | No (refactor only — §10.1 graceful degradation) |
| File listing | Add `ProjectManagementMixin` | No |
| Web search | Add `ExternalToolsMixin` (conditional registration) | No |
| MCP integration | Add `MCPClientMixin` to ChatAgent | No |
| MCP UI | MCP Server Manager panel in Settings | UI only |
| MCP catalog | Curated server catalog (Playwright, Brave, GitHub, etc.) | Config only |
| Browser | Enable Playwright MCP server | MCP config |
| Email/Calendar | Enable Gmail/Outlook/Calendar MCP servers | MCP config |
| App control | Enable Spotify/Obsidian/etc. MCP servers | MCP config |
| Tool discovery | Agent capabilities discovery API (#440) | Minimal API |
| Tool streaming | Tool argument streaming (#441) | Minimal |

### Milestone B — GAIA Agent SDK: New Capabilities
**Goal:** Build new capabilities in the core SDK that don't exist anywhere today.

| Category | What to Build | Scope |
|----------|--------------|-------|
| **Guardrails** | Tool execution confirmation framework (#438) | New SDK framework — OutputHandler, SSE, threading.Event, UI modal |
| **Cancellation** | Cooperative execution cancellation (#439) | New SDK framework — cancel tokens, cleanup |
| **Screenshot** | `ScreenshotToolsMixin` — cross-platform screen capture | New mixin (PIL.ImageGrab, mss) |
| **VLM for Chat** | Wire VLMToolsMixin into ChatAgent + Agent UI image display | Integration + UI |
| **Computer Use** | Desktop automation (mouse, keyboard, window mgmt) | New mixin (pyautogui, pywinauto) |
| **Voice** | Wire ASR/TTS into Agent UI (MediaRecorder, audio playback) | Integration + UI |
| **Tool categories** | Lazy loading, per-session tool selection | SDK architecture change |
| **Cross-platform** | Windows/Linux/macOS shell compat (#442) | SDK enhancement |
| **Image generation** | Wire SDToolsMixin into ChatAgent | Integration |
| **MCP Auto-Discovery** | Search, find, recommend, and install MCP servers on demand (§13.1) | New SDK feature — npm/GitHub registry search, auto-install flow |
| **SKILL.md Support** | Anthropic-compatible skill persistence — load, save, search, share (§13.2) | New SDK feature — skills directory, RAG integration, format spec |

---

## 0. v2 Architecture: Sidecar-First (supersedes the in-process model below)

> Realizes the **Architecture (v2 — sidecar-first)** section of
> [`agent-ui.mdx`](agent-ui.mdx). The Milestone A/B wiring below assumes agents
> run **in-process** inside the UI backend; v2 inverts that — agents are
> out-of-process **sidecars** and the UI is a **thin host**. Where a section
> below wires a capability into in-process `ChatAgent`, read it as "a capability
> the *sidecar* exposes over REST and the host relays," not "Python loaded into
> the UI backend." PR #1910 (in-UI `EmailProxyAgent`) is **superseded**.

### 0.§ Reading map

§0 grew comprehensive; here's the shape so you can jump to what you need.

- **Foundations:** §0.0 the host is a headless daemon (not the web UI) · §0.3 the
  sidecar supervisor.
- **The agent contract:** §0.1 REST surface (fixed-function + `/query`) · §0.2
  `/query` SSE event schema · §0.4 mid-workflow confirmation · §0.18 dispatch ·
  §0.32 multi-agent orchestration · §0.33 `/query` on the `gaia api` REST server.
- **Autonomy:** §0.22 the scheduler clock · §0.34 autonomy-readiness (the
  human-in-the-loop → policy-driven gap).
- **Structure & framing:** §0.35 architecture-review refinements (two-tier custody
  contract, naming, module seams, v1-hardening scope, render primitives).
- **Auth & security:** §0.6 OAuth forward · §0.11 the three auth legs + per-agent
  authorization · §0.24 third-party **containment** (signing, tiers, egress,
  encrypt-at-rest, audit integrity, taint) 🔒.
- **Shared resources (host custody):** §0.9 memory/RAG/sessions/audit/MCP/model
  table · §0.12 the model-slot broker · §0.19 audit sink · §0.29 store consistency.
- **Data & contracts:** §0.28 the agent manifest schema · §0.29 custody-store
  consistency · §0.30 identifier catalog · §0.31 the `/host/v1/*` callback API ·
  §0.15 contract evolution.
- **Lifecycle & ops:** §0.5 install (+model provisioning) · §0.13 concurrency /
  cancel / failure / reaper · §0.14 one daemon per machine · §0.15 version
  negotiation · §0.16 dev-mode · §0.20 uninstall · §0.21 offline/footprint · §0.22
  autonomy clock · §0.25 daemon lifecycle · §0.26 on-disk layout.
- **Delivery & context:** §0.7 CLI parity · §0.8 what's superseded · §0.10
  migration (strangler-fig + data) · §0.17 eval/test · §0.23 feasibility & build
  order · §0.27 relationship to sibling plans.
- **Open decisions needing sign-off:** confirmation model (§0.4) · memory/RAG home
  (§0.9) · third-party trust-root + containment (§0.24) 🔒.

### 0.0 "The host" is a headless daemon, not the web UI

Throughout §0, **the host** is a **headless custody/supervisor daemon** — a
distinct, always-on machine process — *not* the web UI. It serves `/host/v1/*`
(custody: OAuth/memory/RAG/sessions/audit — §0.9, §0.11, §0.19), supervises
sidecars (§0.3), and holds the scheduler clock (§0.22). The **thin web UI** and the
**`gaia <agent>` CLI** are *both* thin clients that attach to this one daemon
(machine-wide, discovered per §0.14). This split is load-bearing:

- **CLI-only / no-UI works.** `gaia email "what did I say about X last week"` and
  its audit writes reach `/host/v1/*` because the daemon — not the browser tab — is
  the custody server. Without this split, any sidecar spawned while the UI is closed
  would have no callback target (the exact hole §0.11/§0.19 exist to prevent).
- **The daemon is the always-on component** that survives the web UI closing, owns
  the machine-wide single-instance registry, and is the wake-up owner for autonomy
  (§0.22). Starting the UI or a CLI command **auto-starts the daemon** if not
  already running.

### 0.1 The agent REST contract (one per sidecar)

Every agent sidecar exposes the **same** HTTP contract, presenting the agent's
**full capability set** (`specification.html`, schema 2.x, is the reference — the
deterministic core *and* the advanced tier: task extraction, follow-up tracking,
daily briefing, scheduled send/snooze, sender prioritization, inbox profiling,
persistent preferences). Two co-equal surfaces:

- **Fixed-function endpoints** — the *deterministic core* as one call each
  (`POST /v1/<agent>/<capability>`: triage, search, pre-scan, archive, calendar-
  create, …). No LLM in the loop; for scripted/integrator use. **Not every
  capability is a fixed-function call** — the spec's `Capability → surface` table
  marks the advanced/personalization/detect-and-reason tier **"agent only"**, so
  those are reached via `/query` + the §0.22 scheduler, *not* a deterministic
  endpoint. (So "exposes ALL capabilities" is true of the *sidecar*, split across
  the two surfaces — not "every capability has a fixed endpoint.")
- **`POST /v1/<agent>/query` — the agent loop, exposed over REST like any other
  endpoint.** NL request in; the sidecar reasons and chains tools into multi-step
  workflows; **response is `text/event-stream` (SSE)**. This is the surface the
  UI's chat experience and the `gaia <agent>` CLI both drive.
- `GET /health`, `GET /version` — readiness + contract-version handshake.
- Connector surfaces: self-service OAuth routes (bare integrators) **and**
  forwarded-connection intake `POST /v1/connections` gated by `X-Gaia-UI: 1`
  (the Agent UI host path — see §0.6).

**`/query` request body (define it — currently only the response is specced):**
`{ query, run_id, context, model?, provider?, max_steps? }`. Two rules that avoid
real bugs:

- **The host mints `run_id`**, not the sidecar. §0.13's `cancel/{run_id}` and the
  confirmation flow key off it; if the sidecar minted it, a "Stop" pressed before
  the first SSE event would have no id to cancel (a race window). Host-minted
  `run_id` is cancellable from the instant the request is sent.
- **Context is pushed in the body**, not pulled. The host owns the transcript
  (§0.9) and passes the relevant slice as `context`; the sidecar stays stateless
  and never reads other sessions back over the callback (which §0.11's scoping
  would forbid anyway). The `GET /host/v1/sessions` callback is host-internal, not
  a sidecar transcript-access path — so a pushed slice and a pulled one can't
  disagree.

### 0.2 `/query` SSE event schema

One JSON object per SSE `data:` line, discriminated on `type`:

| `type` | Payload | UI effect |
|---|---|---|
| `status` | `{message}` | progress line / spinner label |
| `token` | `{delta}` | stream assistant text |
| `tool_call` | `{tool, args}` | "using tool" card |
| `tool_result` | `{tool, render?, data}` | if `render` set (e.g. `email_pre_scan`), draw the typed card from `data`; else a generic result card |
| `needs_confirmation` | `{run_id, action, summary, confirm_url}` | show approve/deny; on approve POST the token (see §0.4) |
| `final` | `{answer, usage}` | finalize the message |
| `error` | `{detail, status}` | surface the actionable error verbatim |

`render` replaces the in-process SSE "fence injection" hack: the sidecar declares
the card type explicitly, so the host needs no per-tool knowledge. **Frontend
change required (be honest about "thin"):** today `MessageBubble.tsx` renders
cards by *fence-parsing* the assistant text (`STRUCTURED_PAYLOAD_LANGS`); v2 moves
it to a `render`→component map keyed off `tool_result` events. The host stays
generic, but each `render` type needs a frontend component, so a new agent that
introduces a new card ships that component with it.

**Build reality — the loop→SSE seam already exists; this table is a re-spec of it.**
`src/gaia/ui/sse_handler.py` (`SSEOutputHandler(OutputHandler)`) already turns every
agent-loop `console.print_*` call into a typed JSON event on a `queue.Queue` a
streaming endpoint can drain — so `/query` is "run the agent with an SSE handler and
expose the queue as `text/event-stream`," **not** net-new agent instrumentation.
BUT the existing handler emits its *own* vocabulary
(`status`/`step`/`thinking`/`tool_start`/`tool_end`/`tool_result`/`chunk`/`answer`),
which is **not** the vocabulary above. The table is therefore the **target contract**;
a small **translation layer** maps the handler's events onto it. Spec the mapping
explicitly (it is the wire contract integrators depend on) rather than assuming the
two vocabularies already agree — they don't.

### 0.3 Host: the per-agent sidecar supervisor

Generalize the email `EmailSidecarManager` into an **`AgentSidecarManager`
registry** keyed by agent id. Per agent: mode (`user` frozen binary / `dev`
uvicorn-from-source), verified download (SHA-256 vs the agent's lock), ephemeral
port (never 4001), lazy spawn on first use, health + `/version` handshake,
tree-kill on shutdown, one shared instance per agent. Host routes:

- `GET  /v1/agents` — installed + catalog (hub mirror, §0.5).
- `POST /v1/agents/{id}/install` · `DELETE /v1/agents/{id}` — one-click install/uninstall.
- `ANY  /v1/<agent>/*` — reverse-proxy to that agent's running sidecar (lazy-start),
  preserving SSE for `/query` and the sidecar's own status codes + actionable detail.

**Build reality — this is a NEW streaming proxy, not "generalize the existing one."**
Today's `EmailSidecarProxy` is a synchronous `requests.Session` that ends every call
in `resp.json()` (`src/gaia/ui/email_sidecar/proxy.py`) — it **fully buffers** and
has no streaming, no client-disconnect propagation, no cancel path. SSE passthrough
(§0.13 cancel-on-disconnect + the synthetic-crash `error` event) must be rebuilt on
`httpx.AsyncClient(stream=True)` + `StreamingResponse`. Standard work, but net-new —
the deterministic (buffered) proxy is what's reusable; the streaming path is not.

### 0.4 Mid-workflow confirmation (over SSE, no WebSocket)

Destructive steps keep the single-use **confirmation-token gate**. Flow:

1. Sidecar reaches a gated step, emits `needs_confirmation{run_id, action, summary, confirm_url}` and **pauses the run** (kept alive server-side, keyed by `run_id`).
2. UI shows approve/deny with the literal `summary` (recipient/subject/body, etc.).
3. On approve the host `POST`s to `confirm_url` (`/v1/<agent>/query/{run_id}/confirm`) with the minted token; the sidecar resumes emitting on the **same** SSE stream. Deny ends the run with a `final` "skipped" answer.

Trade-off: the resume model makes an in-flight run **stateful** in the sidecar (a
`run_id`→continuation map with a TTL), which cuts against the email contract's
explicit **"HTTP, stateless"** ethos and is fragile across dev-reload / crash /
uninstall. The alternative — emit `needs_confirmation`, *end* the stream, let the
host call the deterministic endpoint (`/send` with the token) itself, then issue a
fresh `/query` carrying the prior context to continue — keeps the sidecar
**stateless** at the cost of the host stitching the workflow across calls.

**Recommendation: stateless stop-and-hand-off for v1** (preserves the sidecar's
stateless design; the host, which already holds session context, orchestrates the
continue). Promote to the resume model **only if** real workflows prove they need
many confirmations in one unbroken run and host-side stitching is too clumsy.
*Decision pending sign-off.*

**Approve-what-you-saw invariant (critical).** In the stateless model the sidecar
*re-reasons* on the continuation, so it can produce a plan that **diverges from the
action the user just approved** — the approved summary and the re-executed step are
no longer guaranteed identical. The payload-fingerprint token protects a single
`/send`, but not the workflow continuation. Resolution: the continuation must
**resume the exact approved step**, not re-plan it — either scope the confirmation
token to the whole approved multi-step segment, or have the host pass the sidecar
the exact prior tool-plan/step state so it executes rather than re-decides. This
constraint holds under *either* confirmation model and is a hard requirement.

**Which artifacts belong to which model (avoid the mixed-model trap).** `run_id` is
the **streaming-run handle** and exists under *both* models — it is what §0.13's
`cancel/{run_id}` targets. The `confirm_url = /v1/<agent>/query/{run_id}/confirm`
in the numbered flow above is **resume-model-only** (it keeps a paused run alive).
Under the **recommended stateless model** there is no paused run to resume: a
`needs_confirmation` event *ends* the stream, the host performs the deterministic
call (`/send` with the token) itself, then issues a **fresh `/query`** carrying the
approved-step state to continue. So if stateless is adopted, drop `confirm_url` and
the server-side run-continuation store; keep `run_id` purely for cancellation.

### 0.5 Agent Hub mirror (install / uninstall)

The UI embeds a catalog mirror (existing `agentHub.ts` / `AgentInstallDialog` /
`AgentHubGrid` are the starting point). **Install** = resolve the agent's
platform binary from its lock → verified download (SHA-256) → cache under
`~/.gaia/agents/<id>/` → register so it appears as a launchable agent.
**Uninstall** = stop any running sidecar → remove the cached binary + registration.
Reuses the email sidecar's verified-fetch + lifecycle primitives; **no Node on the
runtime path**. **Install/update/forward are trust decisions — SHA-256 verifies
integrity, not authenticity; they must go through the signing + tier + least-
privilege + containment model in §0.24 before a third-party agent is trusted or
handed a live connection.**

**Install must provision the model, not just the binary (cold-start).** A freshly
installed agent whose model (e.g. `Gemma-4-E4B`) isn't downloaded spawns fine, then
its first `/query` dies deep in the broker/Lemonade path with model-not-found — the
hidden-cold-state failure CLAUDE.md warns about (#1655). The agent's lock/manifest
**declares its required model(s)**; install (or first spawn) pulls them **through the
host broker** (§0.12) with progress in the UI, before the agent is marked launchable.
Define the full **cold sequence**: open UI → no daemon (start it, §0.25) → no agents
(hub grid) → install agent (binary **+ model**) → optional connector consent (§0.6,
§0.24) → first `/query`.

### 0.6 OAuth: host owns consent + refresh, forwards to the sidecar

`grants.json` keeps a single writer. The host runs the OAuth consent flow once and
**forwards** the pre-authenticated connection *out* to each agent sidecar via the
sidecar's `POST /v1/connections` intake (`X-Gaia-UI: 1`; refresh-token/client-secret
are inputs, never returned). Sidecars never run the UI-facing consent write. A bare
integrator (no host) may instead use the sidecar's self-service connector routes.

**Two things the current code makes non-trivial (flag for the build):**

- **Role inversion.** Today `/v1/connections` is a *host-side intake* — external
  apps forward connections INTO gaia (`src/gaia/ui/routers/connectors.py:94`
  `forwarded_router`; `src/gaia/connectors/api.py:266` `import_forwarded_connection`).
  In v2 the host becomes the *forwarder OUT*, and the **sidecar** exposes the
  intake (it bundles `gaia.connectors`). The primitive exists; the direction and
  the per-agent wiring are new.
- **Token refresh ownership.** With N sidecars sharing one Google grant, each
  holding a refresh token would race to refresh and rotate each other out. The
  single-writer host should **own refresh** and forward short-lived *access*
  tokens (re-forward on expiry), so sidecars never touch the refresh token.

### 0.7 CLI ↔ sidecar parity

`gaia <agent>` becomes a thin client of the **host daemon** (§0.0), exactly like the
web UI: it **auto-starts the daemon if none is running, then attaches to it** — it
does **not** run `AgentSidecarManager` or spawn the sidecar itself. The daemon owns
sidecar lifecycle (§0.3) and custody; the CLI just calls the **identical** contract
through it — `gaia email "triage my inbox"` drives `POST /v1/email/query` exactly as
the UI does, via the same daemon. One contract, one supervisor, no second in-process
code path to keep in sync. (If the CLI spawned its own sidecar, that sidecar's
callback base-URL would point at the ephemeral CLI process and its `/host/v1/audit`
+ memory writes would vanish when the command exits — the §0.0 hole. The daemon
being the sole spawner is what closes it.)

### 0.8 What's superseded

- In-process agent construction in `src/gaia/ui/_chat_helpers.py` (the
  `agent_type=…` factory) → replaced by proxying to sidecars.
- `EmailProxyAgent` + the in-UI tool loop (PR #1910) → replaced by relaying to the
  sidecar's `/query`.
- Fat UI routers whose logic belongs to an agent (chat loop, scheduling) → migrate
  into the relevant sidecar; the host keeps UI serving, shared-user-data custody
  (§0.9), and sidecar supervision.

### 0.9 Shared services (host custody) — the biggest open call

Not everything in the fat backend is "agent logic." Three chunks are **user-scoped
data that spans agents** and would fragment/corrupt if each sidecar owned a copy —
so they belong to the host's custody layer (same single-writer rationale as OAuth),
queried by sidecars rather than duplicated:

| Current router | LoC | v2 home (recommended) |
|---|---|---|
| `memory.py` | ~1634 | **Host custody** — one user memory store; sidecars query it. Per-*agent* private memory (e.g. email session prefs) stays in the sidecar. |
| `documents.py` (RAG) | ~739 | **Host custody** — one document/RAG store the user uploads into; any agent queries it. |
| `sessions.py` | ~368 | **Host** — session index **and the durable conversation transcript**. See below: a stateless, uninstall-able sidecar cannot be the system of record for chat history. The host feeds the relevant transcript to the sidecar as input per `/query`. |
| `connectors.py` | ~977 | **Host** — OAuth custody (§0.6). |
| `files.py` | ~665 | **Host** — upload storage the UI serves. |
| `mcp.py` | ~349 | **Host** — the user-configured MCP *server registry* is shared (like connectors); each sidecar connects to the servers its agent needs. Host owns the config, not the connections. |
| (Lemonade — **all** model families) | — | **Host broker** — single-tenant per model slot; a host-owned queue serializes loads of LLM **and** embedder / VLM / ASR / TTS / SD across agents *and* host-custody RAG (§0.12). The most-contended shared resource. |
| (audit trail) | — | **Host custody sink** — consequential actions (incl. autonomous + fixed-function + direct-integrator paths) are appended to a host-owned log, NOT kept agent-private, or the observability dashboard is blind and uninstall erases the record (§0.19). |
| `hub.py`, `agents.py`, `system.py`, `tunnel.py` | ~1700 | **Host** — supervision, settings, remote access. |
| `chat.py` | ~304 | **Sidecar** — agent chat loop moves into the owning agent. |
| `goals.py`, `schedules.py` | ~522 | **Split** — the trigger **registry + cron clock is host** (the daemon is the always-on wake-up owner); the job **executes in the owning sidecar**, spawned at fire time (§0.22). A reaped sidecar can't fire its own cron, so the clock cannot live inside it. |

**Conversation history must be host custody (was a contradiction).** An earlier
draft had the sidecar own message history — but §0.3 makes the sidecar *ephemeral*
(lazy-spawn, tree-kill, dev-reload, and **uninstall deletes the cached binary**),
and §0.4 leans on it being *stateless*. A disposable process cannot be the system
of record for the user's chat log: uninstalling or crashing an agent would destroy
it, and the stateless `/query` model needs the host to *replay* context anyway. So
the **host owns the transcript** (extending the session index it already owns); the
sidecar is fed the slice it needs per call and persists nothing durable itself.

**Session ownership across clients.** Run-isolation (§0.13) namespaces concurrent
runs *inside* the sidecar, but two clients (two UI tabs, or UI + CLI) pointed at the
**same** host-owned session would interleave writes into one transcript — braided
turns or a lost-update race. The host needs a session-focus model: **one active
writer per session** (others read-only/observing), or **per-client sessions** so two
tabs are simply two conversations. Cheap to decide now, confusing to retrofit.

**Alternative considered:** make user-memory and RAG their *own* dedicated
sidecars (purest "all logic in a sidecar"). Rejected for v1 — it multiplies
cross-process user-data writers (the exact problem the OAuth single-writer rule
avoids) and adds two more supervised processes before we've proven one. Revisit if
the host custody layer grows its own heavy reasoning. **This is the single
decision that most shapes the migration — needs explicit sign-off.**

### 0.10 Migration path (strangler-fig — no big-bang)

Retire the ~19K-LoC backend incrementally; the app stays shippable throughout.
**This is code migration — existing user *data* needs its own step:**

0. **Data migration (one-time, idempotent, versioned).** The current `~/.gaia`
   state predates host custody: today's transcripts (`sessions.py` store) have no
   agent tag, and the single memory store has no host/agent-private partition.
   Migrate on first v2 launch: existing sessions → host session index with a
   default agent tag; existing memory → host **user** memory (the conservative
   default — it was cross-agent already), with a documented rule for what becomes
   agent-private. Stamp an on-disk schema version so the migration is detectable and
   runs once. Without this, upgraders lose past chats or see them reattach to a
   wrong/null agent, and memory either leaks cross-agent or is stranded.
1. **Email first** — already sidecar-shaped; add `/query` (SSE) and route the UI's
   email session through it. All other agents remain in-process, untouched.
2. **Host supervisor** — generalize `EmailSidecarManager` → `AgentSidecarManager`
   + hub install/uninstall, so in-process and sidecar agents run side by side.
3. **Per-agent migration** — move one agent at a time behind the proxy; delete its
   in-process router logic only after its sidecar ships.
4. **Extract shared services** — lift user memory / RAG / session index into the
   host custody layer (§0.9) as agents stop owning them in-process.

No step requires all agents migrated at once; each is independently releasable.

### 0.11 Auth: three legs (client↔daemon, daemon↔sidecar, sidecar↔daemon)

The §0.0 daemon split creates **three** trust legs, all currently **undefined** in
the code (`email_sidecar/router.py`/`proxy.py` do no auth; the port is loopback-only).

- **Client → daemon (new with the §0.0 split — 🔒).** The daemon is now a standalone
  process exposing, on a loopback port, the whole custody API (`/host/v1/memory|rag|
  sessions|audit`) **and** the `ANY /v1/<agent>/*` proxy that can drive any sidecar.
  The UI and CLI are *external clients* of it, and loopback is not an auth boundary
  (same threat model as below) — so without a credential, **any local process can
  read the entire cross-agent memory/RAG/transcript store or drive any sidecar
  through the proxy.** The daemon mints a **client-auth token** at startup, stored
  `0600` in its `~/.gaia/host/instance.json` (§0.14); UI and CLI read it and present
  it on every daemon call. (A unix-domain socket with SO_PEERCRED is a stronger
  alternative where available.) This is distinct from the per-spawn secret below,
  which is daemon↔sidecar only.
- **Per-spawn secret (daemon → sidecar).** Loopback binding is **not** an auth
  boundary — any local process (another user on a shared box, a browser tab via
  CSRF/DNS-rebinding, a malicious `npm postinstall`) can `POST /v1/<agent>/query
  "archive everything"` on an unauthenticated port that can send mail. The host
  **mints a random secret per spawn**, passes it to the sidecar via env at launch,
  and the sidecar **requires it** (bearer header) on every request — fixed-function,
  `/query`, and `/confirm`. `X-Gaia-UI: 1` is CSRF hygiene, not authentication.
  (In the daemon-less **bare-integrator** topology of §0.6, the process that
  launches the sidecar supplies this launch secret — the sidecar always requires
  it; only the injector changes.)
- **The reverse contract (sidecar → host).** The custody model (§0.9) needs
  sidecars to *read* host-owned data. Define a first-class host callback API —
  `GET /host/v1/memory`, `POST /host/v1/rag/query`, `GET /host/v1/sessions/{id}` —
  that the sidecar calls, authenticated with the **same per-spawn secret** (the
  host injects its own callback base-URL + the secret at launch). Symmetric to the
  forward proxy; without it, "the host stores it, the sidecar queries it" has no
  transport and agents silently re-fork their own copies.
- **Authorization, not just authentication (🔒 the decisive one).** The per-spawn
  secret only proves "the host spawned me" — it does not say *which* agent, and
  unscoped callback routes turn the custody store into a **single-reader
  exfiltration surface**: a hub-installed third-party agent could read the entire
  cross-agent user memory, the whole RAG corpus, and *any* session transcript by id
  — including other agents' conversations. **The host must bind the secret to the
  agent id at mint time and scope every callback per-agent:** `/host/v1/memory` and
  `/host/v1/rag` return only rows tagged to that agent (or an explicitly
  user-granted shared scope), and `/host/v1/sessions/{id}` verifies the session
  belongs to the caller. Shared-memory/RAG read access becomes a **per-agent grant**
  surfaced at install, like connector grants. This is an architecture-level security
  decision — settle it before the callback contract is frozen.
- **Secret delivery.** Passing the secret via env is readable by other same-user
  processes on some OSes — the exact "another local process" threat the auth exists
  to stop. Prefer a pipe/inherited fd or a short-lived `0600` file over the
  environment.

### 0.12 Shared model backend — a host-owned model-slot broker

Lemonade is **single-tenant per model slot** (this is why evals must run serially —
CLAUDE.md). N sidecars each loading models independently will **race-evict each
other** exactly as concurrent evals do — chaotic ctx-size errors and
`model_load_error` in production, not just CI. The custody layer must arbitrate the
*most*-contended shared resource: a **host-owned broker** that serializes model
loads and grants a **model-slot lease** per request/run. Agents request inference
through the broker (or the host proxies the backend), never contend directly. A
hard requirement for more than one concurrently-active agent.

- **All model families, not just the chat LLM.** Host-custody RAG needs the
  **embedder** to serve `/host/v1/rag/query`; a VLM agent needs the VLM; voice
  (host I/O) needs Whisper/Kokoro; SD needs SDXL. A host RAG call mid-`/query`
  evicting the chat model is the exact silent-ctx-cap regression #1030 warns about.
  The lease must cover embedder + LLM + VLM (+ voice/SD) together for a run —
  host-custody RAG/VLM/voice are broker *clients*, not privileged bypass paths.
- **Priority + preemption.** Serialization prevents corruption, not stalls. A
  Phase-C autonomous brief runs in-sidecar and also draws the slot, so it can block
  the user's interactive turn behind it. The broker needs **interactive > background
  priority** (and preemption), or a background job silently makes a chat turn wait.
- **Affinity + legibility.** A hot-model **affinity hint** keeps agents sharing a
  model from reloading on every switch; the host emits a **`switching model…`
  status event** so an unavoidable evict+reload stall (seconds, worse on NPU) is a
  visible state, not a frozen UI.

### 0.13 Concurrency, cancellation & failure semantics

- **Run isolation.** §0.3 mandates one shared sidecar per agent, but a `/query`
  run is a stateful multi-step loop and today's confirmation store is a single
  process-wide dict. Two simultaneous callers (UI + a scheduled brief, two tabs)
  must not collide: **namespace all per-run state by `run_id`** (token store, tool
  state), forbid shared mutable module state, and document a **parallelism limit**
  (or a per-run worker). "Shared instance" must not silently imply "safely reentrant."
- **Cancellation.** Define `POST /v1/<agent>/query/{run_id}/cancel`; the host
  propagates a UI "Stop" **and** a dropped SSE connection to it (a relayed stream
  hides the client disconnect from the sidecar), and the agent loop checks a
  cancel flag between tool steps — so a stopped run stops burning the model slot
  and fires no further tools.
- **Failure semantics.** On sidecar crash mid-SSE the host injects a synthetic
  terminal `error` event (the UI otherwise sees a truncated stream with no
  `final`), cleans up the orphaned `run_id`/session-index entry, and surfaces the
  actionable cause. Download/spawn failure fails loud via the hub (§0.5).
- **Lifecycle/resource limits.** Every installed agent is a long-lived process
  holding a port + memory + a model-slot claim. Lazy-spawn is not enough: add an
  **idle-timeout / LRU reaper** and a **cap on concurrent live sidecars**, or ten
  installed agents = ten resident processes.

### 0.14 One daemon per machine; the daemon holds one sidecar per agent

Because the **daemon (§0.0) is the sole spawner/supervisor**, the old "UI and CLI
race to spawn rival sidecars" problem is structurally gone — no client spawns a
sidecar. What remains is single-instancing the *daemon* itself and letting clients
find it:

- **One daemon per machine.** A daemon lockfile/registry
  (`~/.gaia/host/instance.json` → pid + client-auth token — see §0.11) makes the
  first UI/CLI invocation start the daemon and every later one **attach**. Two
  daemons would re-create two writers into `grants.json` and two forwarders, so the
  lock is what guarantees a **single forwarder-of-record**.
- **One sidecar per agent, owned by the daemon.** The daemon keeps its own
  per-agent registry (`AgentSidecarManager`, §0.3) → pid + ephemeral port +
  per-spawn secret; UI and CLI never see it, they go through the daemon's proxy. So
  "one shared instance per agent" is now a property of the single daemon, not a race
  between client processes.

### 0.15 Contract-version negotiation at install + render fallback

`GET /version` exists but the policy on mismatch does not. Installing an agent
built against contract 3.x into a 2.x host would silently diverge on the SSE schema,
`render` types, and `/v1/connections` shape. Define: the **host advertises a
supported contract-major range**; hub **install rejects out-of-range agents with a
loud, actionable error** (never a silent partial-compat install); and the frontend
renders an explicit **"unsupported card"** fallback for an unknown `render` type
rather than nothing.

**Custom cards are first-party in v1.** A sidecar binary cannot inject a React
component into the pre-built, signed thin-UI bundle without a dynamic
component-load path (itself a security surface) — so **custom `render` types are
first-party / AMD-verified only in v1**; a third-party agent's novel card
gracefully degrades to the generic result card via the fallback above. Don't plan a
dynamic-component-load path unless/until that's explicitly sanctioned.

**Evolution doesn't stop at the install gate.** Install negotiates only the contract
*MAJOR*; §0.25's daemon self-update then makes skew inevitable (the daemon updates,
installed sidecars don't). Cover the after-install cases:

- **Reverse callback skew (new daemon → old sidecar).** §0.25 versions the
  *client↔daemon* API; the mirror case — an old installed sidecar calling a changed
  `/host/v1/*` — is uncovered. **Version the callback API too**, symmetric to §0.25:
  refuse + prompt reinstall on a MAJOR the daemon can't serve, or hold N-1 compat.
- **Unknown SSE event `type` (§0.2).** A newer agent emitting a new top-level event
  `type` to an older host must **surface a visible "unsupported event," never silently
  drop it** (the CLAUDE.md no-silent-fallback rule) — parity with the `render`-type
  fallback above.
- **Additive vs deprecation.** A new endpoint/capability/event is a backward-compatible
  **MINOR**; removing one needs a stated **deprecation/sunset window**, not a silent
  break. (The MAJOR gate alone can't express either.)

### 0.16 Dev-mode discovery for *unpublished* agents

`GAIA_<AGENT>_MODE=dev` runs a *known* agent from source, but `AgentSidecarManager`
is keyed by hub id + lock — a brand-new agent an author is building has no catalog
entry, no lock, no binary, so it can't be registered or spawned, defeating "the UI
doubles as dev mode." Add a **local-agent registration path**: point the manager at
a source dir + port (skip SHA/lock, no catalog entry) so an in-development agent is
launchable and appears in the UI before it is ever published.

### 0.17 Testing & eval strategy (`/query` + the distributed seams)

`/query` is a multi-step LLM loop — the most LLM-affecting surface, which CLAUDE.md
mandates evals for — but v2 also adds a **daemon, a cross-process broker, an SSE
relay, three auth legs, and a data migration**, each with its own failure modes. The
strategy spans four tiers:

- **`/query` behavior (eval).** A **sidecar eval harness** drives `/query` over REST
  and asserts on the **event sequence** (`tool_call` → `tool_result` →
  `needs_confirmation` → `final`), not just a final string; eval baselines move into
  each agent's hub package. Runs stay **serial** against the shared broker (§0.12) —
  the per-agent sidecar model must not reintroduce concurrent model-slot contention.
- **The distributed seams (deterministic unit/integration, no LLM):**
  - *SSE relay* — streaming passthrough (assert no buffering), cancel-on-disconnect
    (§0.13), and the synthetic-crash `error` event, against a fake sidecar.
  - *Auth legs (§0.11)* — assert **all three** reject a missing/wrong credential
    (client→daemon token, daemon→sidecar per-spawn secret, sidecar→daemon callback),
    and that callback authorization is **per-agent scoped** (agent A cannot read
    agent B's memory/session) — the security property, tested as a boundary, not a
    mock that only proves "we called it."
  - *Broker (§0.12)* — a concurrency test proving two agents requesting different
    models **serialize** rather than race-evict (the property CLAUDE.md's serial-eval
    rule protects), plus priority (interactive preempts background).
  - *Daemon lifecycle (§0.25)* — stale-`instance.json` reclaim after a killed pid,
    atomic write, and daemon restart-on-version-skew draining in-flight runs.
- **Data migration (§0.10 step 0)** — **idempotency + cold-state** test (CLAUDE.md's
  "test from the user's real initial state"): run the one-time migration twice, and
  from a *real pre-v2* `~/.gaia` fixture, asserting transcripts/memory land tagged
  correctly and a second run is a no-op.
- **End-to-end golden path** — one on-hardware test per the `gaia-testing` tiers: UI
  (or CLI) → daemon → sidecar → Lemonade, a real `/query` inbox triage, confirming
  the `email_pre_scan` card renders and a destructive step surfaces the confirmation
  gate. This is the "the call is valid, not just invoked" proof for the whole chain.

### 0.18 Dispatch — which sidecar answers a free-form message

`/query` "is what the UI's chat drives," but with N installed sidecars nothing
chooses the target. `gaia email "…"` is explicit; the UI's single chat box is not —
the "host renders, sidecar reasons" split has a hole exactly where the user types.
Pick one model and put it in the call graph:

- **(a) Explicit per-session agent selection** (recommended v1) — the UI's active-
  agent picker names the sidecar; matches the CLI; zero extra LLM cost.
- **(b) A routing *sidecar*** the host calls first — flexible, but routing is itself
  an LLM loop, so it draws the model slot (§0.12) and adds a hop before every turn.

Cross-agent requests ("summarize this and email it") need either the user to switch
agents or an orchestrator agent that itself calls other sidecars — **designed in
§0.32** (later than v1, but a first-class agent on the same contract, so the v1
picker isn't mistaken for the end state).

### 0.19 Audit trail — a host-custody sink, not agent-private

The observability dashboard (security-model.mdx) promises "everything the agent
did," but v2 moves the *acting* into sidecars, so the host sees only what crosses
its SSE relay. Three blind spots: fixed-function calls from the CLI/integrators
bypass the host; Phase-C autonomous schedules fire in-sidecar with no host in the
loop; and email's action log is agent-*private* (§0.9) so the dashboard can't read
it and uninstall deletes it. **Every consequential action** (send, archive,
calendar-create, autonomous or scripted) must be appended to a **host-owned audit
log** via `POST /host/v1/audit` (per-agent scoped, §0.11) — not just host-relayed
`/query`. Otherwise the trail is a partial, self-erasing view.

### 0.20 Uninstall — data lifecycle

§0.5 uninstall stops the sidecar + removes the binary, but is silent on data.
Define the policy explicitly (tie to §0.11 per-agent scoping so "this agent's data"
is a well-defined set):

- **Forwarded connection** — the host **withdraws/revokes** the OAuth connection it
  forwarded, so a removed agent doesn't retain live mailbox access.
- **Host-custody transcripts** tagged to the agent — offer **keep or delete** (they
  survive the binary; dangling "ghost" sessions that can't reopen are the failure to
  avoid); on reinstall, keep = reattach.
- **Agent-private state** (`~/.gaia/agents/<id>/`, e.g. the action log) — wipe by
  default (state a default), with an option to retain.
- **Shared user-memory rows** the agent wrote — governed by its §0.11 grant scope.

### 0.21 Offline / sideload install + install footprint

- **Sideload.** Every §0.5 path *downloads* from the lock's `baseUrl` — but GAIA is
  privacy-first and targets air-gapped + OEM-preloaded deployments (a stated Phase-D
  goal). Add a **local install source**: point the manager at a pre-staged directory
  of verified binaries (still SHA-check against the lock, skip the network), covering
  the *first* agent too. This also unblocks OEM factory-image bundling — cheap to
  reconcile now rather than retrofit.
- **Footprint (honest downgrade).** The email binary is ~90 MB **only because** its
  freeze `--exclude-module`s torch/transformers/faiss/scipy/pandas — legal solely
  because email runs no in-process ML (it calls Lemonade over HTTP;
  `packaging/freeze.py`). Any agent that needs an in-process embedder/VLM/SD (RAG,
  vision) **cannot** use those excludes → multi-GB per binary. And PyInstaller
  produces self-contained bundles with **no cross-binary linking**, so a "shared
  runtime layer the per-agent binaries link against" is **not a PyInstaller
  capability** — it needs a different packaging strategy (a shared base venv, a
  host-side model-runtime service the sidecars call, or non-frozen deployment).
  Treat §0.21 shared-runtime as **aspirational**, not a config tweak, and prefer
  keeping in-process ML *out* of sidecars (route it through the host model broker,
  §0.12) to preserve the lightweight-binary property.

### 0.22 Autonomy — the host holds the clock, the sidecar does the work

§0.9 runs autonomy (schedules/goals) in the owning sidecar, but §0.13 idle-reaps
sidecars and caps live ones — so a reaped email sidecar has nothing alive at 8am to
fire the daily brief, and the job silently never runs (passing every unit test).
Resolve by splitting the clock from the work:

- The **host daemon (§0.0) owns the trigger registry + cron clock** — always-on, it
  is the wake-up owner. Schedule *metadata* is host custody.
- At fire time the host **spawns the owning sidecar** (if not resident) and hands it
  the job over `/query` (or a fixed endpoint), then lets the reaper reclaim it after.
- Alternatively, mark specific agents **pinned-resident** (exempt from the reaper +
  live-cap) when sub-minute latency matters. State which agents qualify; default is
  wake-on-fire, since holding every autonomous agent resident defeats §0.13.

Either way the wake-up owner is the daemon, never a reapable sidecar.

### 0.23 Feasibility & build sequencing (grounded in the current code)

A feasibility pass against the codebase found **no fatal blocker** — the design is
buildable — but the "thin" framing rests on some primitives that are reuse and
others that are genuinely net-new. Naming which is which sets an honest build order.

**Already exists (de-risks the plan):**

- **The agent-loop → SSE seam** (`src/gaia/ui/sse_handler.py` `SSEOutputHandler`) —
  `/query` reuses it (§0.2 build note), not net-new instrumentation.
- **Mid-workflow confirmation over SSE** — the pause/resume primitive §0.4 describes
  (`_confirm_event`, `_user_input_queue`, `permission_request` events) **ships
  in-process today**; the only new work is carrying the Event across the process
  boundary (which is exactly what makes a resumed run stateful — the §0.4 tension is
  real, not hypothetical).
- **OAuth forward + short-lived-token refresh** — `import_forwarded_connection` and
  `get_access_token` (`src/gaia/connectors/api.py`) already exist and the refresh
  engine is client-neutral, so §0.6's "host refreshes, forwards short-lived tokens"
  maps onto real code.

**Net-new, load-bearing (sequence first):**

1. **Lemonade broker (§0.12) — the critical path.** Today the only lock is an
   *in-process* `_downloads_lock` (`lemonade_client.py`); "one model slot" literally
   means last-writer-wins across processes. The host broker (serialized loads +
   cross-process lease + priority) has **zero existing scaffolding** and gates the
   plan's headline (more than one concurrently-active agent). Build + prove first.
2. **Streaming reverse-proxy (§0.3, §0.13)** — net-new `httpx` streaming + cancel;
   the current proxy buffers. Prove with a one-endpoint **freeze spike** (SSE through
   a PyInstaller binary is unproven here, though low-risk — uvicorn loops/protocols
   are collected in the freeze) before committing.
3. **`/query` endpoint + event-vocab translation (§0.1, §0.2)** — de-risked by the
   SSE seam, but the sidecar has no `/query` today and the handler's vocabulary must
   be translated to the §0.2 contract.
4. **CLI rewrite (§0.7)** — `gaia email` is fully in-process (`cli.py` builds
   `EmailTriageAgent` and calls `process_query`); converting it to a daemon client is
   a real rewrite that must stay behaviorally identical.
5. **Forward-OUT intake on the sidecar (§0.6)** — primitive exists; the sidecar
   `/v1/connections` intake route + role inversion is new wiring (low risk).

Build order: **broker + streaming-proxy spike → `/query` + vocab → CLI → OAuth
forward-out**, migrating email first (§0.10) as the reference.

### 0.24 Security & privacy — containing a sidecar that is itself the adversary

The prior auth work (§0.11) *authenticates* the sidecar↔host channels well, but the
plan had **no story for containing a hostile agent** — critical the moment
**one-click, third-party** hub agents (§0.5) are installable. A security review
surfaced these. The first three are one coupled trust-root + containment decision;
**settle before the first third-party agent ships (🔒 @kovtcharov-amd).**

- **Sign the lock/catalog — SHA-256 alone is not authentication (🔒).** The hash
  proves a binary matches the lock; it says nothing about *who wrote the lock*. A
  controlled catalog/`baseUrl`/CDN can serve attacker code + a matching hash, or a
  known-vuln *older* version. Sign the lock/catalog with an AMD key and verify the
  signature *before* trusting any hash inside it; add **anti-rollback** (record
  highest-installed version, refuse silent downgrade); third-party publishers get a
  publisher signature + TOFU pin.
- **Tier-gate + least-privilege the OAuth forward (🔒).** §0.5 one-click + §0.6
  forward means a third-party agent can be handed a **live mailbox token**. Nothing
  consults the Phase-D trust tiers (Verified/Community/Experimental) before
  forwarding, and the forward hands the *whole* grant. Make forwarding + shared
  scopes **tier-aware**, require an **explicit install consent** naming the exact
  OAuth scopes forwarded, and forward the **minimum** scope the agent declares.
- **Constrain sidecar network egress (🔒 the decisive containment gap).** A hostile
  sidecar holds a mailbox token *and*, as an ordinary process, unrestricted outbound
  network — it can read mail through the sanctioned connection and POST it anywhere,
  invisible to the callback-authorization model (which guards host custody, not the
  agent's own sockets). This is the difference between "sandboxed agent" and
  "trusted arbitrary code with your mailbox," and it negates "100% local."
  No-network-by-default + a **declared, install-surfaced egress allowlist** (the
  manifest names hosts, e.g. `googleapis.com`) enforced via a host-controlled
  network namespace/proxy. **Carve-out (do not sever the control channel):** the
  daemon's own loopback endpoints — the §0.11 callback API, the §0.12 broker, and
  the reverse-proxy — are an **always-allowed control channel**, separate from the
  external-host egress allowlist. The proxy enforcement variant satisfies this
  automatically (it *is* the host); a network-namespace variant must explicitly
  plumb the daemon socket into the namespace, or it would cut the very
  callback/broker paths the custody model depends on.
- **Encrypt data at rest — but separate the two secret classes (don't break §0.11).**
  `0600` stops other *users*, not the threats that matter on a single-user desktop
  (stolen laptop, synced backup, same-user malware). Two distinct classes:
  - **Durable custody secrets** — OAuth **refresh tokens** (`grants.json`) and the
    custody stores (memory/RAG/transcripts) → **OS keychain** (Keychain/DPAPI/
    libsecret) + **encrypt at rest**. Pull Phase-C's "encrypted credential vault"
    **forward to whenever §0.6 lands**, not v0.23. These are the high-value,
    long-lived secrets a stolen disk exposes.
  - **The §0.11 client-auth token** (`instance.json`) — must stay **client-readable**
    (UI/CLI read it to call the daemon), so it legitimately remains **`0600` in
    `instance.json`**: it is *ephemeral, re-minted every daemon startup*, so losing
    it is harmless. Do **not** move it to the keychain — that would break the read
    path §0.11 relies on. (The daemon↔sidecar per-spawn secret is likewise ephemeral;
    keychain is for durable secrets only.)
- **Make the audit log tamper-evident.** §0.19 appends actions to a host sink, but
  nothing enforces append-only — a compromised process can rewrite/truncate it,
  defeating the observability promise. Hash-chain / rolling-MAC each entry (sealing
  the prior), restrictive perms, write-only exposure to agents, gap/rewrite detection
  surfaced in the dashboard.
- **Cross-agent prompt-injection taint (name it in §0.18 now).** The email agent
  hardens *its own* body-as-data, but the cross-agent path (orchestrator, "summarize
  this and email it") carries untrusted mailbox content from agent A into agent B's
  `/query`, where it can drive B's destructive action — compounded by §0.4's
  re-plan divergence. **Taint must travel with the data across the boundary** (mark
  cross-agent-sourced context untrusted so the receiver treats it as data), with the
  confirmation gate as backstop. A hard requirement for the orchestrator, not v1.
- **MCP auto-install must not reintroduce the supply-chain hole.** §0.11 names "a
  malicious `npm postinstall`" as a threat, yet §13.1 proposes auto-installing
  `mcp-server-*` from npm/GitHub on demand — unpinned/unsigned npm runs arbitrary
  postinstall code. Restrict auto-discovery to a **curated, version-pinned,
  integrity-checked allowlist**; never auto-execute an unpinned package; disable
  lifecycle scripts; gate installs behind the same signature/tier model as agents.
- **Signed updates + re-consent.** §0.5 specs install/uninstall but not **update**.
  Silent catalog auto-update is the classic vector (push malicious "update" to an
  agent already granted mailbox + memory scope). Make update a first-class,
  **signature-verified** op (reusing the signed lock), with anti-rollback and
  **re-consent when the new version widens declared scopes/egress**.
- **Telemetry vs "100% local."** Any phone-home (incl. Phase-D "cost savings
  telemetry") punctures the privacy promise and rides the same egress as above.
  **Local-only aggregation by default**; any network telemetry is explicit opt-in
  with a visible disclosure. State the default here.

The through-line: the plan authenticates the channel but must also **contain the
endpoint**. Containment (egress + least-privilege connection + host-custody scope +
signed/tiered trust root) is the security decision that gates third-party agents.

### 0.25 The daemon's OWN lifecycle — birth, death, control, update

§0 specs the daemon's *responsibilities* (custody, broker, supervision, clock) and
its *singleton identity* (§0.14) but treats it as a process that simply exists. It
never says how it starts, recovers, is controlled, or updates — the single
highest-leverage hole, because a daemon that can silently die strands the scheduler
clock (§0.22), the broker (§0.12), and every sidecar, undercutting the "always-on
agent" headline.

- **Birth + rebirth (fixes the §0.0↔§0.22 contradiction).** §0.0 says the daemon
  "auto-starts on the first UI/CLI call," but §0.22 makes it the always-on cron
  clock — after an overnight reboot with no human present, nothing starts it and the
  8am brief never fires. Register the daemon with the **OS process manager**
  (launchd/LaunchAgent, Windows Scheduled Task/service, systemd user unit) so it
  **starts at login/boot and restarts on crash** — a supervisor-of-the-supervisor.
  The Phase-C system-tray app is the natural long-term home, but it lands two phases
  after the daemon ships, so name the **interim** manager. On restart, define whether
  pinned-resident sidecars (§0.22) are respawned or left to lazy-spawn.
- **Stale `instance.json` recovery.** On SIGKILL/OOM/power-loss the §0.14 lock file
  is left pointing at a dead pid / freed port; the next client either hangs or
  attaches to an unrelated process now on that port. On attach, **liveness-check the
  pid and probe the port + auth token before trusting the file**; if dead, atomically
  reclaim it. Write it temp-then-rename so a crash mid-write can't corrupt it.
- **Control surface + cross-tier diagnostics.** Add `gaia daemon status|stop|restart|
  logs` — an always-on process needs a way to see/stop/recover it (`gaia kill` is too
  blunt; it also kills the clock). Give each sidecar a log file under its state dir,
  **stamp `run_id` (§0.1) into every daemon/sidecar/relay log line**, and extend
  `gaia diagnostics` to gather daemon + all sidecar logs correlated by `run_id` — so
  a run spanning UI→daemon→sidecar→Lemonade is reconstructable and third-party-agent
  bug reports are actionable, not "it froze."
- **Daemon↔client version skew on update.** §0.15 negotiates the *agent* contract;
  the *UI/CLI↔daemon* boundary is uncovered. An app update replaces the CLI/UI while
  the **old daemon keeps running**, so the new client attaches to a stale host API.
  **Version the host API**; on attach, mismatch the client can't speak → **drain
  in-flight runs and cleanly restart the daemon** into the new binary. Decide where
  the daemon binary ships (core wheel vs hub) so "update the daemon" has an owner.

### 0.26 On-disk state layout & update survival

The `~/.gaia` layout is specified piecemeal (`host/instance.json` §0.14, `agents/<id>/`
§0.5, custody stores + schema version §0.10, keychain secrets §0.24) with **no single
map** — and daemon *config* (pinned agents §0.22, broker priorities §0.12, egress
allowlists §0.24, reaper timeout + live-cap §0.13) has **no assigned home**. Add one
layout table classifying every path as **runtime-ephemeral** (rebuildable —
`instance.json`, caches, spawned-sidecar records), **durable user data** (must be
backed up — custody: OAuth/memory/RAG/transcripts/audit), or **config**
(daemon settings + per-agent settings), and state the **update-survival guarantee**:
custody + config survive an app update; ephemeral is rebuilt. One source of truth
prevents an update from silently orphaning grants, memory, or pinned-agent config.

### 0.27 Relationship to sibling plans (supersedes / depends / reconcile)

v2 is a fundamental architecture change, so several sibling plan docs — which still
describe the in-process model — must be superseded or reconciled, or a reader
following the wrong one builds the wrong thing.

**Supersedes (the sibling doc is now wrong for out-of-process agents):**

- **`connectors.mdx` + `email-sidecar-agent-ui.md` — "the sidecar reads
  `grants.json` directly" is superseded by §0.6.** Those docs bundle
  `gaia.connectors` into the freeze so the binary can read the grant + keyring;
  v2 **inverts** this — the host owns consent+refresh and *forwards a short-lived
  access token* to the sidecar via `/v1/connections`; **sidecars never touch the
  refresh token or the ledger.** A sidecar reading `grants.json` is exactly the
  cross-process-writer race §0.6 removes. (Most material — it changes what the
  email cutover builds.)
- **`security-model.mdx` "localhost-only is the trust boundary; CLI+UI are
  TRUSTED" is superseded by §0.11/§0.24.** v2 states loopback is **not** an auth
  boundary and mandates the three auth legs + per-agent authorization + egress
  containment. An implementer reading only `security-model.mdx` would wrongly
  conclude localhost binding suffices.
- **`email-sidecar-agent-ui.md` decision 4 (fixed-call forwarding only, no
  `/query`) is superseded** — v2 makes `/query` (SSE) the primary UI chat surface
  (§0.10 step 1). PR #1910's `EmailProxyAgent` is already flagged superseded (§0
  header); the standalone doc needs a top banner pointing here.
- **`email-sidecar-agent-ui-implementation.md` "the UI backend spawns/owns the
  sidecar; `EmailSidecarManager` lives in `src/gaia/ui/`" is the *interim*
  (email-first, §0.10 step 1) shape, not the v2 end state.** In v2 the
  supervisor **relocates into the headless daemon** (§0.0/§0.3/§0.14); the UI and
  CLI only attach.

**Reconcile (decide the boundary, then state it in both docs):**

- **`autonomy-engine.mdx` — the v2 host daemon IS the Autonomy Engine's always-on
  background service.** Both independently define an always-on process with a cron
  clock; they are the **same process** (recommended). The shipped in-UI
  `/api/schedules` router (`schedules.py`, #550) moves per §0.9: the **clock +
  trigger registry are host/daemon**, the job **executes in the owning sidecar**
  spawned at fire time (§0.22). Self-scheduling writes to the host clock, never
  sidecar-local state.
- **MCP server ownership (`connectors.mdx` mirrors servers for an in-process
  `MCPClient`).** v2 split: the **host owns the MCP server *registry/config***
  (user-configured, shared — §0.9); a **sidecar spawns/manages its own client
  connections** to the servers its agent needs. The host does not proxy MCP
  traffic; it owns the config the sidecar reads (per-agent scoped, §0.11).
- **`setup-wizard.mdx` model download vs §0.5 per-agent install-time provisioning.**
  Two model-download triggers exist (wizard first-run, profile-level; §0.5 per-agent
  at install). Decide ownership + sequencing: the wizard handles the *base profile*
  model at first run; §0.5 handles *additional* per-agent models at install, both
  pulling through the host broker (§0.12); daemon auto-start (§0.25) must compose
  with wizard first-run detection so they don't both fight over `setup-state.json`.

**Depends on / same work (cross-link, not conflict):**

- **`agent-ui-hub-publish.mdx` Part 2 "in-app Agent Hub + dynamic install" IS §0.5.**
  Same work — cross-link them. Its multi-component model also resolves "is the UI an
  agent?" cleanly: **the UI is an `app`, not an installable agent.** Two requirements
  v2 layers on top that the hub docs must carry: install **provisions the model, not
  just the binary** (§0.5 cold-start), and **sign the lock/catalog + anti-rollback**
  (§0.24) — the hub docs specify SHA-256 + tiers, but SHA is integrity, not
  authenticity.

### 0.28 The agent manifest — the load-bearing artifact (was undefined)

~8 sections say "the manifest/lock declares X," but no section defined it — and the
artifact that exists today (`hub/agents/npm/agent-email/binaries.lock.json`) is a
**binary-integrity map only** (`schemaVersion`, `agentVersion`, `baseUrl`, per-platform
`{filename, executable, sha256, size}`) — it carries **none** of the policy/capability
fields §0 leans on. So the plan conflated two different artifacts:

- **The lock** = binary integrity (exists). SHA/signature-verified (§0.24).
- **The manifest** = capability + policy declaration (**new; define it**). It drives
  *authorization* decisions (egress, scopes, tier), so it **must ride inside the
  §0.24 signature envelope** — the recommended shape is a separate `manifest.json`
  **referenced by digest from the signed lock**, so its security fields can't be
  swapped independently of the binary. A manifest outside the signed envelope is
  forgeable and worthless for authorization.

**Schema (fields → the section that requires each):**

| Field | Required by |
|---|---|
| `id`, `displayName`, `agentVersion` | §0.3/§0.14 registry key, §0.5 |
| `contractMajor` (+ supported range) | §0.15, §0.1 |
| `requiredModels[]` (LLM + embedder/VLM/…, min ctx) | §0.5 cold-start, §0.12 broker pull |
| `capabilities[]` + which are `fixedFunctionEndpoints[]` vs agent-only | §0.1 two-surface split, §0.18 dispatch |
| `renderTypes[]` the agent emits | §0.2, §0.15 (first-party gate + fallback) |
| `oauthScopes[]` (minimum forwarded) | §0.6, §0.24 least-privilege + install consent |
| `egressAllowlist[]` (hosts) | §0.24 |
| `trustTier` (Verified/Community/Experimental) | §0.24 tier-gating |
| `publisher` + signature / TOFU pin | §0.24 (SHA ≠ authenticity) |
| `mcpServers[]` needed | §0.9, §0.24 curated allowlist |
| `sharedScopes[]` requested (memory/RAG read grants) | §0.11, §0.20 |
| `pinnedResident` + `schedules[]`/triggers | §0.22 |
| `devSourcePath` (dev-mode) | §0.16 |

**Authoring + validation.** The publisher checks `manifest.json` into the hub package
next to the lock. The install-time validator **fails loud** on: unknown
`contractMajor`, missing `requiredModels`, a capability with no matching `renderType`/
endpoint, or egress/scope fields absent when `trustTier != Verified`. This one artifact
unblocks §0.5, §0.12, §0.15, §0.18, §0.20, and §0.24 at once.

### 0.29 Custody store consistency model (concurrent daemon + N sidecars)

§0.9 moves memory/RAG/sessions/transcript/audit into host custody, read/written by the
daemon **and** N sidecars — but only `grants.json` got a single-writer rule (§0.6). The
same rule must generalize, and one case is a hard correctness requirement:

- **Single physical writer.** All custody writes go **through the daemon**; sidecars
  *request* writes via `/host/v1/*`, they never touch the files. This extends §0.6's
  single-writer rationale to memory, RAG, sessions, and audit — so N sidecars writing
  "shared user-memory rows" (§0.20) still resolve to one physical writer.
- **Serialized audit appends (hard requirement of §0.24's hash-chain).** §0.24's
  tamper-evident chain needs a **strict total order** — "each entry seals the prior."
  N sidecars `POST /host/v1/audit` concurrently would both seal entry *k* and **fork
  the chain**, which the gap-detector reads as tampering. The daemon must serialize
  appends behind a **single append queue**, or the tamper-evidence guarantee is
  undefined.
- **Storage engine.** The current UI stores are SQLite (single-writer; `SQLITE_BUSY`
  under concurrent writers). Specify **WAL + `busy_timeout`** (or a host-side write
  queue) and RAG **read-during-index** isolation, or "host stores it, sidecars query
  it" hits lock contention the first time two agents write.

(Sessions is partly covered — §0.9 already flags the two-client interleave and proposes
a single-active-writer focus model.)

### 0.30 Identifier catalog

`run_id` is the model (host-minted §0.1; cancel target §0.13; confirm key §0.4;
log-correlation stamp §0.25). The rest need the same rigor — minter / scope / lifetime /
consumer:

- **`session id`** — host-minted, host-owned (§0.9); it is the **authorization key** the
  callback verifies (`/host/v1/sessions/{id}`, §0.11), so leaving its mint point +
  per-client-vs-per-session semantics undefined (the open §0.9 focus decision) leaves a
  *security check keyed off an undefined id*. Resolve with §0.9.
- **`action_id`** — mint on **every consequential action**; it's the handle the §0.19
  audit entry and §0.20 uninstall-scoping ("withdraw/revoke this action") both need, and
  it appears nowhere in §0 today. Stamp it into the audit record.
- **`batch_id`** — batch-archive/undo handle (email spec); define its mint + lifetime.
- **per-spawn secret vs client-auth token** — already cleanly separated (§0.11/§0.24); no
  gap.

### 0.31 The `/host/v1/*` callback API (the reverse contract, specified)

§0.11/§0.9/§0.19/§0.29 lean on the daemon's reverse contract but never list it. Like the
manifest (§0.28), it's load-bearing and must be pinned. Every route requires the
per-spawn secret **and** resolves the calling agent id from it (§0.11 authorization);
every response is scoped to that agent (or its granted shared scope).

| Route | Shape | Scope / notes |
|---|---|---|
| `POST /host/v1/rag/query` | `{query, k}` → `{chunks[]}` | agent-scoped corpus (or `sharedScopes`); read-during-index isolation (§0.29) |
| `GET /host/v1/memory` | `?scope=&query=` → `{items[]}` | agent-private memory, or user memory only if the manifest declares the `sharedScopes` grant (§0.28/§0.11) |
| `POST /host/v1/memory` | `{scope, item}` → `{id}` | write goes through the daemon's single writer (§0.29) |
| `GET /host/v1/sessions/{id}` | → `{transcript_slice}` | daemon verifies the session belongs to the caller (§0.30 `session id` = authz key) |
| `POST /host/v1/audit` | `{action_id, action, summary, ts}` → `{seq}` | serialized append onto the hash-chain (§0.29); write-only to agents (§0.24) |
| `POST /host/v1/models/lease` | `{model}` → `{lease}` / 429 | the §0.12 broker slot lease; blocks/queues by priority |
| `POST /host/v1/agents/{id}/invoke` | `{query|capability, args}` → SSE/JSON | **orchestrator-scoped** agent-to-agent call (§0.32); not open to ordinary sidecars |

**Versioning:** this API carries its own MAJOR, negotiated on the sidecar↔daemon leg and
subject to the §0.15 evolution rules (new-daemon/old-sidecar skew). **Errors:** every
route fails loud with an actionable, typed error (`403` unauthorized/wrong-scope, `409`
audit-chain conflict, `429` no model slot, `503` store unavailable) — never a silent
empty result, per the no-silent-fallback rule. The daemon's **client-facing** API
(UI/CLI → daemon: sessions list, agent install/uninstall, `/query` proxy) is a separate
surface under the client-auth token (§0.11), versioned per §0.25.

### 0.32 Multi-agent orchestration (the headline "complex workflow automation")

The stated goal is workflow automation that "reasons and calls the necessary tools,"
including **cross-agent** ("summarize this doc and email it to Bob"). §0.18 rightly
ships a v1 **explicit agent picker** (one sidecar per turn), but the cross-agent case
needs a design, or the headline capability has no home. Shape it now even if it lands
after v1:

- **An orchestrator is itself an agent (a sidecar), not host logic.** Keeping the host
  thin (§0.0), the orchestrator is a first-class agent whose `/query` **plans a
  multi-step, multi-agent workflow** and invokes other agents. The host stays the
  router/custodian; the reasoning lives in the orchestrator sidecar.
- **Agent-to-agent calls go *through the host*, never sidecar-to-sidecar directly.**
  The host mediates so every hop is **authorized (§0.11 per-agent scope), audited
  (§0.19 `action_id`), broker-leased (§0.12), and taint-tracked** — a direct
  sidecar→sidecar mesh would bypass all four. Add a host route
  (`POST /host/v1/agents/{id}/invoke`, client-auth + orchestrator-scoped) so the
  orchestrator reaches sub-agents under the same controls as any client.
- **Taint + confirmation are the safety backbone (§0.24 / §0.4).** Untrusted content
  read by agent A (a mailbox body) that becomes agent B's input is **marked tainted so
  B treats it as data, not instructions** (§0.24 cross-agent injection), and **every
  destructive cross-agent step keeps the confirmation gate** (§0.4 approve-what-you-saw)
  — the orchestrator cannot launder an injected instruction into an un-approved send.
- **Cost is real and must be legible.** A cross-agent workflow is N agent loops + M
  model switches on one slot (§0.12) — the orchestrator's own loop plus each sub-agent's.
  Surface it as streamed `status` (which agent is working) and hold **interactive
  priority** so a foreground orchestration preempts background jobs (§0.12).
- **Sequencing:** v1 = explicit picker (§0.18); the orchestrator is a **later,
  first-class agent** built on the same contract — not a special host mode. This keeps
  "the UI is thin, the sidecar reasons" intact even for multi-agent flows.

### 0.33 The GAIA REST API agent server exposes `/query` too (`src/gaia/api/`)

`/query` is not UI-specific — it belongs on **every agent-serving REST surface**. GAIA's
OpenAI-compatible API server (`src/gaia/api/openai_server.py`, `gaia api`) is a
first-class front-door for **programmatic** agent access; today it serves
`POST /v1/chat/completions` (agents-as-models), `GET /v1/models`, and an in-process
`/v1/email/*` mount. It must also expose the **agentic `/query` loop**.

- **Add `POST /v1/<agent>/query` (SSE) to the API server**, the *same* contract
  (§0.1/§0.2) — so a REST consumer gets tool-calling, multi-step workflows, and the
  typed event stream, not only OpenAI-style chat. **One agent-loop implementation:** the
  API server **proxies to the sidecar** (via the daemon/broker, §0.3/§0.12), exactly as
  the UI and CLI do — it does not run its own in-process loop.
- **Relationship to `/v1/chat/completions`.** Keep both: chat-completions is the
  OpenAI-SDK-compatible surface (drop-in for existing tooling); `/query` is the richer
  **agentic superset** (SSE tool events + `needs_confirmation` + workflows). Consumers
  choose by need; neither is removed.
- **Supersedes the API server's in-process email mount.** `openai_server.py:143`
  mounts `gaia_agent_email.api_routes` in-process — the same in-process pattern the UI
  cutover removed (this surface was explicitly left out of PR #1910's scope). Under v2
  it likewise becomes **sidecar-backed** (proxy to the email sidecar), so core stays
  lightweight and there is one email implementation.
- **Auth is stricter here than the loopback daemon (🔒).** `gaia api` is a
  *network-exposed* surface (and reachable remotely via the tunnel), unlike the
  daemon's loopback custody API — so `/query` on it must sit behind the API server's
  **API-key auth** and still enforce the §0.4 confirmation gate + §0.24 containment on
  every destructive step. Do not inherit the loopback trust assumptions of §0.11 here.

### 0.34 Autonomy readiness — infrastructure-ready, policy-hostile

GAIA's roadmap is "always-on background agent" (Phase C), so assess v2 against **full
autonomy** (the agent acting unattended, on its own initiative). The verdict: the
architecture is a **strong foundation but not yet a fit for full autonomy**, because its
safety + interaction model is **human-in-the-loop by construction**.

**Already fits (the substrate is right):** the always-on **daemon** (§0.0/§0.25) +
**scheduler clock** (§0.22); **tamper-evident audit** for unattended review (§0.19/§0.24);
**persistent memory** (§0.9); **interactive > background broker priority** so autonomous
jobs yield to the user (§0.12); and **containment** (egress/least-privilege/taint, §0.24)
— the exact guardrails high-stakes unattended action needs.

**Fights full autonomy (all assume a human is present) — the autonomy layer to design:**

1. **Confirmation assumes a watcher.** §0.4's "approve-what-you-saw" pauses the SSE
   stream for *synchronous* approval — but unattended there's nobody to approve. Full
   autonomy needs a **policy / pre-authorization model**: the user pre-grants categories
   of action (e.g. "auto-archive promotions," "auto-decline conflicting invites") with
   undo + audit, *replacing* per-action approval when unattended. This is the central
   missing piece.
2. **Cron-only triggering (§0.22).** Autonomy is largely **event-driven** ("urgent mail
   arrived → act"); needs a trigger/event bus + mailbox-watch, not just a clock.
3. **No long-lived goals / self-initiation.** The contract is request → `/query` → done;
   autonomy means the agent **initiates** from standing objectives it tracks over time
   (`goals.py` is noted moving to a sidecar, §0.9, but autonomous goal-pursuit is
   undesigned).
4. **No async escalation.** An unattended agent that hits uncertainty should **notify and
   resume later** when the user answers — but §0.4 confirmation is synchronous. Needs a
   notify-and-resume path (push notification → deferred approval → continue).
5. **No autonomy levels.** There are third-party *trust tiers* (§0.24) but no **graduated
   autonomy** (observe → suggest → act-with-undo → act-freely) governing what an agent may
   do unattended, per capability.
6. **Ephemeral vs. continuous.** §0.13 reaps idle sidecars; a *monitoring* agent doesn't
   fit "spawn-at-fire, reap-after." Pinned-resident (§0.22) is the exception, not a
   first-class monitoring model.

**Structural implication:** autonomy is a **new layer above the agent contract**, not a
change to it — a host-side **autonomy engine** (policy store + event bus + goal tracker +
async-escalation + autonomy-level enforcement) that drives `/query`/fixed-function calls
on the user's behalf under pre-authorization, reusing audit (§0.19) + containment (§0.24)
+ broker priority (§0.12) as its guardrails. It also reconciles with `autonomy-engine.mdx`
(§0.27): that engine **is** this layer, hosted in the daemon. Sequence it after the
human-in-the-loop v1 — the v1 confirmation model is the *safe default*, and the autonomy
layer is what lets the user progressively hand off.

### 0.35 Structural refinements (from the architecture review)

A holistic architecture review found the design **structurally sound** — the daemon is
a *coherent custodian* (everything it owns needs a single writer, a single arbiter, or
is the trust root — one responsibility, not ten; and §0.9's rejection of dedicated
memory/RAG sidecars is *correct* because decomposing would reintroduce the multi-writer
problem the design exists to avoid), the sidecar↔host coupling is a *versioned runtime
cycle*, not a build cycle, and the deferrals are honest. The remaining work is framing +
trimming premature hardening. The refinements (priority order):

1. **Sidecar independence is a two-tier contract, not a flat claim.** The sidecar owns
   no durable state (§0.9), so custody-backed `/query` needs a host. Make it honest:
   publish **`/host/v1/*` (§0.31) as a first-class, third-party-implementable custody
   interface** (a third party can bring their own custodian), and formalize the
   **bare-integrator degraded tier** (§0.6) as a *shipped, tested product* with a
   capability matrix — **works without a host:** fixed-function + stateless `/query`;
   **needs a host:** memory, RAG, sessions/transcript, audit. Converts the biggest hidden
   coupling into an explicit contract tier. (Reflected in the mdx overview.)
2. **Naming: never call the daemon "thin."** "The host" = the **custodian daemon**, the
   most responsibility-dense process; "thin" is the UI only. (Applied across the docs.)
3. **Assert the daemon's internal module seams.** One *process* is right; one *module*
   would rot into a monolith. Commit — even inside the process — to separated
   **custody-store / broker / supervisor / proxy-router / clock** modules with defined
   interfaces. Mark the **broker (§0.12) as the one designed to be later-extractable** to
   its own process: it arbitrates a hardware resource contended by non-agents (host RAG
   embedder, voice, SD), a different axis + lifecycle from data custody.
4. **Slogan: "one *agent* contract, many front-doors; two *control-plane* contracts
   behind it"** (the callback §0.31 and the client↔daemon §0.11/§0.25) — so B/C get
   first-class versioning/auth, not footnote status. (Reflected in the mdx.)
5. **Trim three pieces of premature hardening to "third-party gate, not v1 build"**
   (all additive later; acceptable under the first-party single-user threat model):
   - **Audit:** v1 = a plain **append-only** host-owned log (the single writer already
     gives order); the §0.24 **hash-chain** + §0.29 **global single-append-queue** are
     the third-party addition, not day-one.
   - **Egress:** v1 = the **proxy-enforced allowlist** (§0.24); the full **network-
     namespace sandbox** is research-grade/per-OS — defer.
   - **Broker:** v1 = **priority *queueing*** (foreground jumps the queue); defer
     **mid-run preemption** (§0.12) — you can't cleanly pause a llama-server generation.
6. **Make the render boundary real (layering fix).** The "thin UI" has a hidden
   compile-time dependency on first-party agents' card set (§0.2/§0.15). Ship a **small
   fixed set of generic render primitives** (table / key-value / list / image / diff)
   once; **agents default to them**, a bespoke component is the *first-party exception* —
   so "the thin UI renders *any* agent" is true rather than an ever-growing first-party
   component map.

None re-shape the core; they make the existing shape teachable and stop three claims
(thin UI, one contract, independent sidecar) from being louder than the design supports.

---

## 1. Current GAIA SDK Capability Inventory

### 1.1 Agents

| Agent | Class | Location | Tools/Mixins |
|-------|-------|----------|-------------|
| **ChatAgent** | `ChatAgent(Agent, RAGToolsMixin, FileToolsMixin, ShellToolsMixin, FileSearchToolsMixin)` | `agents/chat/agent.py` | RAG, file watch, shell commands, file search |
| **CodeAgent** | `CodeAgent(ApiAgent, Agent, CodeToolsMixin, ValidationAndParsingMixin, FileIOToolsMixin, CodeFormattingMixin, ProjectManagementMixin, TestingMixin, ErrorFixingMixin, TypeScriptToolsMixin, WebToolsMixin, PrismaToolsMixin, CLIToolsMixin, ExternalToolsMixin, ValidationToolsMixin)` | `agents/code/agent.py` | Full-stack dev, CLI, testing, web, Prisma, external search |
| **BlenderAgent** | `BlenderAgent(Agent)` | `agents/blender/agent.py` | 3D scene manipulation via MCP |
| **JiraAgent** | `JiraAgent(Agent)` | `agents/jira/agent.py` | Jira issue management |
| **DockerAgent** | `DockerAgent(MCPAgent)` | `agents/docker/agent.py` | Docker container management via MCP |
| **SDAgent** | `SDAgent(Agent, SDToolsMixin, VLMToolsMixin)` | `agents/sd/agent.py` | Image generation + visual analysis |
| **MedicalIntakeAgent** | `MedicalIntakeAgent(Agent, DatabaseMixin, FileWatcherMixin)` | `hub/agents/python/emr/gaia_agent_emr/agent.py` | Medical form processing with VLM |
| **RoutingAgent** | `RoutingAgent` | `hub/agents/python/routing/gaia_agent_routing/agent.py` | Intelligent agent selection |
| **SummarizerAgent** | `SummarizerAgent(Agent)` | `agents/summarize/agent.py` | Document summarization |

### 1.2 ChatAgent Tools (Current — What the Agent UI Uses)

| Tool | Mixin | Description |
|------|-------|-------------|
| `run_shell_command` | `ShellToolsMixin` | Execute terminal commands (whitelisted, read-only) |
| `add_watch_directory` | `FileToolsMixin` | Watch a directory for file changes |
| `query_documents` | `RAGToolsMixin` | Semantic search across indexed documents |
| `query_specific_file` | `RAGToolsMixin` | Query a specific indexed file |
| `search_indexed_chunks` | `RAGToolsMixin` | Low-level chunk search |
| `evaluate_retrieval` | `RAGToolsMixin` | Evaluate RAG retrieval quality |
| `index_document` | `RAGToolsMixin` | Index a document for RAG |
| `index_directory` | `RAGToolsMixin` | Index all documents in a directory |
| `list_indexed_documents` | `RAGToolsMixin` | List all indexed documents |
| `rag_status` | `RAGToolsMixin` | Get RAG system status |
| `summarize_document` | `RAGToolsMixin` | Summarize an indexed document |
| `dump_document` | `RAGToolsMixin` | Dump raw document content |
| *(FileSearchToolsMixin)* | `FileSearchToolsMixin` | Shared file search utilities |

### 1.3 CodeAgent Tools (Available in SDK, NOT in Agent UI)

| Tool | Mixin | Description |
|------|-------|-------------|
| `read_file` | `FileIOToolsMixin` | Read file contents |
| `write_file` | `FileIOToolsMixin` | Write/create files |
| `edit_file` | `FileIOToolsMixin` | Edit existing files (diff-based) |
| `edit_python_file` | `FileIOToolsMixin` | Python-aware file editing |
| `search_code` | `FileIOToolsMixin` | Search code with regex/glob |
| `run_cli_command` | `CLIToolsMixin` | Execute any CLI command (broader than shell_tools) |
| `stop_process` | `CLIToolsMixin` | Stop background processes |
| `list_processes` | `CLIToolsMixin` | List managed background processes |
| `get_process_logs` | `CLIToolsMixin` | Get output from background processes |
| `cleanup_all_processes` | `CLIToolsMixin` | Stop all background processes |
| `execute_python_file` | `TestingMixin` | Execute Python scripts |
| `run_tests` | `TestingMixin` | Run pytest test suites |
| `list_files` | `ProjectManagementMixin` | List files in directory tree |
| `create_project` | `ProjectManagementMixin` | Create project from template |
| `create_architectural_plan` | `ErrorFixingMixin` | Generate architecture plans |
| `create_workflow_plan` | `ErrorFixingMixin` | Generate workflow plans |
| `search_documentation` | `ExternalToolsMixin` | Search Context7 documentation |
| `search_web` | `ExternalToolsMixin` | Web search via Perplexity |
| `list_symbols` | `CodeToolsMixin` | List code symbols (AST) |
| Various TypeScript/Web tools | `TypeScriptToolsMixin`, `WebToolsMixin` | npm, template, Next.js |
| Various Prisma tools | `PrismaToolsMixin` | Database schema management |

### 1.4 Other SDK Capabilities (Not Exposed to Any Agent)

| Capability | SDK Location | Description |
|------------|-------------|-------------|
| **Vision/VLM** | `gaia/vlm/mixin.py` | `analyze_image`, `answer_question_about_image` |
| **Image Generation** | `gaia/sd/mixin.py` | `generate_image`, `list_sd_models`, `get_generation_history` |
| **Audio/ASR** | `gaia/audio/whisper_asr.py` | Speech-to-text (Whisper) |
| **Audio/TTS** | `gaia/audio/kokoro_tts.py` | Text-to-speech (Kokoro) |
| **MCP Bridge** | `gaia/mcp/mcp_bridge.py` | External tool integration via MCP |
| **Database** | `gaia/database/` | `DatabaseMixin` for persistent storage |
| **Multi-provider LLM** | `gaia/llm/providers/` | Claude, OpenAI, Lemonade backends |
| **Agent Routing** | `hub/agents/python/routing/gaia_agent_routing/agent.py` | Intelligent multi-agent routing |

---

## 2. Gap Analysis: Agent UI Agent vs. Modern PC Agent Expectations

### 2.1 Capabilities Users Expect Today

Based on the current landscape (Claude Computer Use, OpenAI Operator, Windows Copilot, etc.):

| Category | Capability | Status | Priority |
|----------|-----------|--------|----------|
| **File System** | Read/write/edit files | MISSING (ChatAgent only has read-only shell + RAG) | P0 |
| **File System** | Create directories, move/copy/rename files | MISSING | P0 |
| **File System** | File search (name, content, regex) | EXISTS via FileSearchToolsMixin | P1 |
| **Shell** | Run shell commands | EXISTS | P0 |
| **Shell** | Background process management | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Web** | Browse URLs, fetch web content | MISSING | P1 |
| **Web** | Search the web | MISSING in ChatAgent (exists in CodeAgent via Perplexity) | P1 |
| **Vision** | Take screenshots of desktop/windows | MISSING | P1 |
| **Vision** | Analyze images/screenshots | MISSING in ChatAgent (exists in SDAgent) | P1 |
| **Vision** | OCR / read text from images | MISSING | P2 |
| **Computer Use** | Click, type, scroll on screen | MISSING | P2 |
| **Computer Use** | Control mouse and keyboard | MISSING | P2 |
| **Computer Use** | Window management (focus, resize, list) | MISSING | P2 |
| **Code** | Read/write/edit code files | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Code** | Run Python scripts | MISSING in ChatAgent (exists in CodeAgent) | P1 |
| **Audio** | Voice input (speech-to-text) | MISSING in Agent UI (SDK exists) | P2 |
| **Audio** | Voice output (text-to-speech) | MISSING in Agent UI (SDK exists) | P2 |
| **Image Gen** | Generate images from prompts | MISSING in ChatAgent (exists in SDAgent) | P2 |
| **Clipboard** | Read/write clipboard | MISSING | P2 |
| **System** | Get system info (OS, CPU, GPU, memory) | PARTIAL (shell commands) | P2 |
| **Browser** | Open URLs in default browser | MISSING | P2 |
| **Notifications** | Desktop notifications | MISSING | P3 |
| **Scheduling** | Schedule tasks, set reminders | MISSING | P3 |
| **App Control** | Launch/close applications | MISSING | P3 |

### 2.2 What Can Be Added to ChatAgent NOW (Reusing Existing SDK)

These capabilities already exist in the codebase and just need to be wired into ChatAgent:

| Capability | Source | Effort | How |
|-----------|--------|--------|-----|
| File read/write/edit | `FileIOToolsMixin` from CodeAgent | **Low** | Add mixin to ChatAgent class |
| Code search | `FileIOToolsMixin.search_code` | **Low** | Included with FileIOToolsMixin |
| List files (tree view) | `ProjectManagementMixin.list_files` | **Low** | Add mixin to ChatAgent class |
| Web search | `ExternalToolsMixin.search_web` | **Low** | Add mixin to ChatAgent class |
| Doc search (Context7) | `ExternalToolsMixin.search_documentation` | **Low** | Add mixin to ChatAgent class |
| Image analysis | `VLMToolsMixin.analyze_image` | **Medium** | Add mixin + VLM model loading |
| Image Q&A | `VLMToolsMixin.answer_question_about_image` | **Medium** | Same as above |
| Image generation | `SDToolsMixin.generate_image` | **Medium** | Add mixin + SD model loading |
| Background processes | `CLIToolsMixin` (run/stop/list/logs) | **Medium** | Add mixin, security review |
| Python execution | `TestingMixin.execute_python_file` | **Medium** | Add mixin, sandbox review |

### 2.3 What Requires New Development

These capabilities don't exist anywhere in GAIA and need to be built:

| Capability | Category | Effort | Notes |
|-----------|----------|--------|-------|
| **Screenshot capture** | Vision | **Medium** | Use `PIL.ImageGrab` (Windows) or platform APIs. New tool mixin. |
| **Web browsing / URL fetch** | Web | **Medium** | `httpx` + BeautifulSoup for content extraction. New tool mixin. |
| **Clipboard read/write** | System | **Low** | `pyperclip` or `win32clipboard`. New tool. |
| **Open URL in browser** | System | **Low** | `webbrowser.open()`. New tool. |
| **Desktop/window control** | Computer Use | **High** | `pyautogui` / `pywinauto` for Windows. Complex, needs careful security. |
| **Mouse/keyboard control** | Computer Use | **High** | `pyautogui`. Very powerful, very dangerous. Requires guardrails (#438). |
| **Window listing/management** | Computer Use | **Medium** | `pywinauto` on Windows, `wmctrl` on Linux. |
| **Voice input (ASR)** | Audio | **Medium** | Wire existing `whisper_asr.py` SDK into Agent UI. WebSocket or MediaRecorder API. |
| **Voice output (TTS)** | Audio | **Medium** | Wire existing `kokoro_tts.py` SDK into Agent UI. Audio playback. |
| **Desktop notifications** | System | **Low** | `plyer` or `win10toast` on Windows. |
| **App launch/control** | System | **Medium** | `subprocess.Popen` for launch, `psutil` for control. Security-sensitive. |
| **Task scheduling** | System | **Medium** | Windows Task Scheduler or `APScheduler`. Persistent. |

---

## 3. Implementation Plan

### Phase 1: Quick Wins — Wire Existing SDK into ChatAgent (1-2 weeks)

Extend `ChatAgent` with existing mixins from CodeAgent and other agents. Minimal new code.

| # | Feature | Mixin to Add | Risk |
|---|---------|-------------|------|
| 1a | File read/write/edit | `FileIOToolsMixin` | Low — already battle-tested in CodeAgent |
| 1b | Code search | *(included in FileIOToolsMixin)* | Low |
| 1c | List files (tree view) | `ProjectManagementMixin` | Low |
| 1d | Web search | `ExternalToolsMixin` | Low — requires Perplexity API key or fallback |
| 1e | Python script execution | `TestingMixin` | Medium — needs sandboxing review |

**ChatAgent class after Phase 1:**
```python
class ChatAgent(
    Agent,
    RAGToolsMixin,         # Existing: document Q&A
    FileToolsMixin,        # Existing: file watching
    ShellToolsMixin,       # Existing: shell commands
    FileSearchToolsMixin,  # Existing: file search
    FileIOToolsMixin,      # NEW: read/write/edit files
    ProjectManagementMixin,# NEW: list_files, create_project
    ExternalToolsMixin,    # NEW: web search, doc search
    TestingMixin,          # NEW: execute Python, run tests
):
```

### Phase 2: Vision & Media (2-3 weeks)

Add image analysis, screenshot capture, and image generation.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 2a | Image analysis (VLM) | Add `VLMToolsMixin`, load VLM model alongside main LLM | Medium — needs VLM model (Qwen3-VL-4B) |
| 2b | Screenshot capture | New `ScreenshotToolsMixin` using `PIL.ImageGrab` + `mss` | Medium — cross-platform |
| 2c | Image generation (SD) | Add `SDToolsMixin`, requires Lemonade SD model | Medium — optional, SD model may not be loaded |
| 2d | Image display in Agent UI | Frontend: render images inline in chat messages | Medium — base64 or file URL serving |

### Phase 3: Web & System (2-3 weeks)

Add web browsing, clipboard, and basic system tools.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 3a | URL fetch / web scraping | New `WebBrowsingToolsMixin` using `httpx` + `BeautifulSoup` | Low |
| 3b | Open URL in browser | New tool using `webbrowser.open()` | Low |
| 3c | Clipboard read/write | New tool using `pyperclip` | Low |
| 3d | System info | New tool using `platform`, `psutil`, `GPUtil` | Low |
| 3e | Desktop notifications | New tool using `plyer` | Low |

### Phase 4: Computer Use (4-6 weeks, separate milestone)

Full desktop automation. This is the most complex and security-sensitive phase.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 4a | Window listing | `pywinauto` (Win) / `wmctrl` (Linux) / `pyobjc` (macOS) | Medium |
| 4b | Window focus/resize | Same as above | Medium |
| 4c | Screenshot of specific window | `PIL.ImageGrab` with window handle | Medium |
| 4d | Mouse click/move | `pyautogui` with coordinate targeting | **High** — needs guardrails |
| 4e | Keyboard typing | `pyautogui.typewrite()` | **High** — needs guardrails |
| 4f | Screen element detection | VLM + screenshot → identify clickable elements | **High** — requires VLM |
| 4g | Browser automation | Playwright via MCP or direct integration | **High** — complex |

### Phase 5: Audio/Voice (2-3 weeks)

Wire existing Whisper ASR and Kokoro TTS into Agent UI.

| # | Feature | Implementation | Risk |
|---|---------|---------------|------|
| 5a | Voice input (push-to-talk) | Browser MediaRecorder → backend → Whisper ASR | Medium |
| 5b | Voice output (TTS) | Backend Kokoro TTS → audio stream → browser playback | Medium |
| 5c | Voice conversation mode | Continuous ASR + TTS for hands-free chat | High |

---

## 4. Cross-Platform Requirements

All capabilities MUST work on Windows, Linux, and macOS:

| Capability | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Shell commands | `cmd.exe` / PowerShell (shell=True) | `/bin/sh` | `/bin/zsh` |
| File operations | `pathlib` (cross-platform) | Same | Same |
| Screenshots | `PIL.ImageGrab` / `mss` | `mss` / `scrot` | `mss` / `screencapture` |
| Clipboard | `pyperclip` (auto-detects) | `xclip`/`xsel` | `pbcopy`/`pbpaste` |
| Window mgmt | `pywinauto` | `wmctrl`/`xdotool` | `pyobjc`/`osascript` |
| Notifications | `win10toast` / `plyer` | `notify-send` / `plyer` | `osascript` / `plyer` |
| Mouse/keyboard | `pyautogui` (cross-platform) | Same | Same (accessibility permissions) |
| Browser open | `webbrowser.open()` (cross-platform) | Same | Same |

---

## 5. Security Considerations

| Risk | Mitigation |
|------|-----------|
| Shell command injection | Whitelist approach (existing), guardrails popup (#438) |
| File write to system paths | PathValidator (existing), restricted allowed_paths |
| Arbitrary code execution | Sandboxed Python execution, no `eval()`/`exec()` |
| Screenshot privacy | User confirmation before capture, no auto-capture |
| Computer use (mouse/keyboard) | Mandatory confirmation per action, visual indicator, kill switch |
| Web requests (SSRF) | URL allowlist, no internal network access |
| Clipboard access | User confirmation, no silent reads |

---

## 6. Issue Tracker

### Already Created (Milestone #15)

| Issue | Title | Status |
|-------|-------|--------|
| [#438](https://github.com/amd/gaia/issues/438) | Tool execution guardrails | Open |
| [#439](https://github.com/amd/gaia/issues/439) | Cooperative execution cancellation | Open |
| [#440](https://github.com/amd/gaia/issues/440) | Agent capabilities discovery API | Open |
| [#441](https://github.com/amd/gaia/issues/441) | Tool argument streaming | Open |
| [#442](https://github.com/amd/gaia/issues/442) | Windows/cross-platform shell compatibility | Open |

### To Create (Phase 1-5)

| Phase | Title | Priority |
|-------|-------|----------|
| P1 | Add FileIOToolsMixin to ChatAgent (file read/write/edit) | P0 |
| P1 | Add ExternalToolsMixin to ChatAgent (web search) | P1 |
| P1 | Add ProjectManagementMixin to ChatAgent (list_files) | P1 |
| P1 | Add TestingMixin to ChatAgent (Python execution) | P1 |
| P2 | Add VLMToolsMixin to ChatAgent (image analysis) | P1 |
| P2 | Screenshot capture tool mixin | P1 |
| P2 | Image display in Agent UI messages | P1 |
| P2 | Add SDToolsMixin to ChatAgent (image generation) | P2 |
| P3 | Web browsing / URL fetch tool mixin | P1 |
| P3 | Clipboard read/write tool | P2 |
| P3 | Open URL in browser tool | P2 |
| P3 | System info tool | P2 |
| P3 | Desktop notifications tool | P3 |
| P4 | Window listing and management tool mixin | P2 |
| P4 | Mouse/keyboard control tool mixin (computer use) | P2 |
| P4 | Browser automation via Playwright | P2 |
| P5 | Voice input (ASR) in Agent UI | P2 |
| P5 | Voice output (TTS) in Agent UI | P2 |

---

## 7. MCP Server Integration

### 7.1 Current MCP Infrastructure in GAIA

GAIA already has a robust MCP client infrastructure:

- **`MCPClientMixin`** (`gaia/mcp/mixin.py`) — Any agent can connect to MCP servers and auto-register their tools
- **`MCPClientManager`** — Manages multiple MCP server connections
- **Config file** — `~/.gaia/mcp_servers.json` for persistent server configuration
- **`MCPAgent`** base class — `agents/base/mcp_agent.py`
- **MCP Bridge** — `gaia/mcp/mcp_bridge.py` exposes GAIA as an MCP server to external tools
- **Existing integrations** — Docker MCP, Blender MCP already implemented

**Gap:** The Agent UI has NO way to manage MCP servers. Users can't add, remove, enable/disable, or configure MCP servers from the UI.

### 7.2 Most Popular MCP Servers (2026 Ecosystem)

Based on real usage data from [MCP Directory](https://mcp.directory/blog/top-10-most-popular-mcp-servers) and [mcpservers.org](https://mcpservers.org/):

#### Tier 1 — Essential (High demand, directly useful for Agent UI)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Filesystem** | `@modelcontextprotocol/server-filesystem` | Secure file operations with configurable access controls | File System |
| **Playwright** | `@anthropic/mcp-playwright` | Browser automation via accessibility snapshots (not screenshots) | Browser |
| **GitHub** | `@modelcontextprotocol/server-github` | Repos, PRs, issues, workflows — full GitHub access | Dev Tools |
| **Desktop Commander** | `desktop-commander` | Terminal command execution + file operations with user control | System |
| **Fetch** | `@modelcontextprotocol/server-fetch` | Web content fetching and conversion to markdown | Web |
| **Memory** | `@modelcontextprotocol/server-memory` | Knowledge graph-based persistent memory for agents | Context |
| **Git** | `@modelcontextprotocol/server-git` | Git repository tools (log, diff, status, blame) | Dev Tools |
| **Sequential Thinking** | `@anthropic/mcp-sequential-thinking` | Structured reasoning for complex problems | Reasoning |

#### Tier 2 — High Value (Popular integrations users commonly request)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Slack** | `slack-mcp-server` | Channel management, messaging, conversation history | Communication |
| **Notion** | `notion-mcp` | Workspace pages, databases, tasks | Productivity |
| **Google Drive** | `google-drive-mcp` | File access, search, sharing | Cloud Storage |
| **PostgreSQL** | `@modelcontextprotocol/server-postgres` | Database queries | Database |
| **Brave Search** | `@anthropic/mcp-brave-search` | Web search (alternative to Perplexity) | Web Search |
| **Context7** | `context7-mcp` | Inject fresh, version-specific code docs into prompts | Documentation |

#### Tier 3 — Windows Desktop Automation (Key for "Computer Use")

| Server | Repo | Description | Platform |
|--------|------|-------------|----------|
| **Windows-MCP** | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | Native Windows UI automation: open apps, control windows, simulate input, capture UI state | Windows |
| **mcp-windows-desktop-automation** | [mario-andreschak/mcp-windows-desktop-automation](https://github.com/mario-andreschak/mcp-windows-desktop-automation) | TypeScript MCP wrapping AutoIt: mouse, keyboard, clipboard, window management | Windows |
| **mcp-windows-automation** | [mukul975/mcp-windows-automation](https://github.com/mukul975/mcp-windows-automation) | 80+ automation tools: app control, system management, natural language commands | Windows |
| **mcp-desktop-automation** | [tanob/mcp-desktop-automation](https://github.com/tanob/mcp-desktop-automation) | Cross-platform desktop automation using RobotJS + screenshots | Cross-platform |

#### Tier 4 — Microsoft Ecosystem (Enterprise)

| Server | Source | Description |
|--------|--------|-------------|
| **Microsoft Learn MCP** | [MicrosoftDocs/mcp](https://github.com/MicrosoftDocs/mcp) | Real-time Microsoft documentation access |
| **Azure MCP Server** | [Microsoft Learn](https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/overview) | Azure resource management via natural language |
| **Azure DevOps MCP** | [Microsoft Learn](https://learn.microsoft.com/en-us/azure/devops/mcp-server/mcp-server-overview) | Work items, PRs, builds, test plans |
| **Windows On-Device Agent Registry** | [Microsoft Learn](https://learn.microsoft.com/en-us/windows/ai/mcp/overview) | Secure discovery of local MCP servers on Windows |

### 7.3 Agent UI MCP Integration Design

#### A) MCP Server Manager Panel (Settings)

The Agent UI Settings modal gets an "MCP Servers" tab where users can:

1. **Browse/add popular servers** from a curated list (Tier 1-2 above)
2. **Add custom servers** by providing command + args + env config
3. **Enable/disable servers** per session or globally
4. **View connected server status** (connected, tools available, errors)
5. **Configure server credentials** (API keys, tokens) with secure storage

#### B) Backend API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mcp/servers` | GET | List configured MCP servers and their status |
| `/api/mcp/servers` | POST | Add a new MCP server configuration |
| `/api/mcp/servers/{name}` | DELETE | Remove an MCP server |
| `/api/mcp/servers/{name}/enable` | POST | Enable/connect a server |
| `/api/mcp/servers/{name}/disable` | POST | Disable/disconnect a server |
| `/api/mcp/servers/{name}/tools` | GET | List tools provided by a server |
| `/api/mcp/catalog` | GET | Get curated list of popular servers |

#### C) ChatAgent MCP Integration

```python
class ChatAgent(
    Agent,
    MCPClientMixin,        # NEW: MCP server connectivity
    RAGToolsMixin,
    FileToolsMixin,
    ShellToolsMixin,
    FileSearchToolsMixin,
    # ... other mixins
):
```

When the Agent UI enables an MCP server, the backend:
1. Calls `agent.connect_mcp_server(name, config)`
2. Tools from the MCP server are auto-registered in the agent's tool registry
3. The agent can now use those tools in its planning/execution
4. Tools appear in the Capabilities panel (#440)

#### D) Curated Server Catalog

Ship a built-in catalog (`~/.gaia/mcp_catalog.json`) with pre-configured popular servers:

```json
{
  "catalog": [
    {
      "name": "filesystem",
      "display_name": "File System",
      "description": "Secure file read/write/search with configurable access",
      "category": "system",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"],
      "requires_config": ["allowed_directories"],
      "tier": 1
    },
    {
      "name": "github",
      "display_name": "GitHub",
      "description": "Repos, PRs, issues, workflows",
      "category": "dev-tools",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": ""},
      "requires_config": ["GITHUB_TOKEN"],
      "tier": 1
    },
    {
      "name": "playwright",
      "display_name": "Browser (Playwright)",
      "description": "Web browsing and interaction via accessibility snapshots",
      "category": "browser",
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-playwright"],
      "tier": 1
    }
  ]
}
```

### 7.4 MCP Issues to Create

| Phase | Title | Priority |
|-------|-------|----------|
| P1 | Add MCPClientMixin to ChatAgent | P0 |
| P1 | MCP server management API endpoints | P0 |
| P1 | MCP Server Manager UI panel in Settings | P0 |
| P1 | Curated MCP server catalog with Tier 1 servers | P1 |
| P2 | MCP server credential secure storage | P1 |
| P2 | Per-session MCP server enable/disable | P2 |
| P2 | MCP server health monitoring and auto-reconnect | P2 |
| P3 | Windows Desktop MCP integration (computer use) | P2 |
| P3 | Windows On-Device Agent Registry (ODR) integration | P3 |

---

## 8. Dependencies

```
Phase 1 (Quick Wins — Existing SDK Mixins)
  └── No external dependencies (reuse existing SDK)

Phase 1-MCP (MCP Server Integration)
  ├── MCPClientMixin (already exists in SDK)
  ├── Node.js/npx (for npm-based MCP servers)
  └── MCP server packages installed on demand

Phase 2 (Vision & Media)
  ├── Lemonade Server with VLM model loaded (Qwen3-VL-4B)
  ├── PIL/Pillow (already in deps)
  └── mss (new dep for cross-platform screenshots)

Phase 3 (Web & System)
  ├── httpx (already in deps)
  ├── beautifulsoup4 (new dep)
  ├── pyperclip (new dep)
  └── plyer (new dep for notifications)

Phase 4 (Computer Use)
  ├── Phase 1 (guardrails MUST be done first)
  ├── Phase 2 (VLM for screen understanding)
  ├── pyautogui (new dep)
  ├── pywinauto (Windows, new dep)
  ├── Playwright (optional, for browser automation)
  └── OR: Windows Desktop MCP servers (external, via MCP)

Phase 5 (Audio/Voice)
  ├── Whisper ASR model loaded in Lemonade
  ├── Kokoro TTS model loaded in Lemonade
  └── Browser MediaRecorder API support
```

## 9. Critical Capabilities Coverage Matrix

The following capabilities were identified as **user-critical priorities**. This matrix
tracks exactly where each is addressed in the plan and flags gaps.

| Critical Capability | Covered? | Where in Plan | Gaps / Issues |
|---------------------|----------|---------------|---------------|
| **Browser control & use** | ✅ Yes | Phase 4g (Playwright), MCP Tier 1 (Playwright MCP) | Playwright MCP is the fastest path. Native Playwright should be deferred. Plan puts this in Phase 4 (too late) — should be Phase 1-MCP. See §9.1. |
| **Web search** | ✅ Yes | Phase 1d (ExternalToolsMixin), MCP Tier 2 (Brave Search) | ExternalToolsMixin requires Perplexity API key — needs free fallback (Brave Search MCP). See §9.2. |
| **Document analysis** | ✅ Yes | Already exists (RAGToolsMixin) | Fully functional: index, query, summarize, dump. Needs no changes. |
| **Document search** | ✅ Yes | Already exists (RAGToolsMixin + FileSearchToolsMixin) | Working: `query_documents`, `search_indexed_chunks`, `query_specific_file`. |
| **Document summarization** | ✅ Yes | Already exists (`summarize_document` in RAGToolsMixin) | Also have standalone `SummarizerAgent`. Could wire summarizer into ChatAgent for long docs. |
| **Document Q&A** | ✅ Yes | Already exists (RAGToolsMixin) | Core feature, fully operational. |
| **Shell command tools** | ✅ Yes | Already exists (ShellToolsMixin), Windows fix done | Whitelist-only, read-only. Need write capability discussion (§9.3). |
| **Guardrails (catastrophic failure prevention)** | ✅ Yes | Issue #438, §5 Security | Design exists but **not yet implemented**. This is the single most important prerequisite for all write/execute capabilities. See §9.4. |
| **Email triage & management** | ❌ **MISSING** | Not in plan | Needs Email/Calendar MCP servers. See §9.5. |
| **Calendar management** | ❌ **MISSING** | Not in plan | Needs Google Calendar / Outlook MCP. See §9.5. |
| **Application control (CUA)** | ⚠️ Partial | Phase 4 (pyautogui/pywinauto) | Plan covers low-level mouse/keyboard but NOT application-specific control patterns. See §9.6. |
| **Popular app demos (e.g. Spotify)** | ❌ **MISSING** | Not in plan | Needs Spotify MCP or CUA workflow. See §9.6. |

### 9.1 Browser Control — Needs Priority Bump

The plan buries browser automation in Phase 4g which is 8+ weeks out. But the
**Playwright MCP server** is a Tier 1 server that works TODAY with the existing
`MCPClientMixin`. This should be promoted to **Phase 1-MCP** (first sprint):

```
BEFORE:  Phase 4g (week 8+) — build native Playwright integration
AFTER:   Phase 1-MCP (week 1) — enable Playwright MCP server
         Phase 4g (later) — build native integration only if MCP insufficient
```

The Playwright MCP provides: navigate, click, fill forms, take screenshots, read page
content — all via accessibility snapshots. This covers 90% of browser use cases.

### 9.2 Web Search — Needs Free Fallback

`ExternalToolsMixin.search_web` requires a `PERPLEXITY_API_KEY` environment variable.
If the user doesn't have one, web search silently fails. The plan says "Low effort"
but doesn't address this.

**Fix:** Prioritize **Brave Search MCP** (`@anthropic/mcp-brave-search`) as the
default web search. It's free-tier capable and runs as a standard MCP server. Fall
back chain should be:
1. Brave Search MCP (free, no API key for basic usage)
2. Perplexity (if API key available, via ExternalToolsMixin)
3. Fetch MCP (raw URL fetch + markdown conversion as last resort)

### 9.3 Shell Commands — Write Capability Gap

Current `ShellToolsMixin` is **read-only** by design (whitelist: `ls`, `cat`, `grep`,
`find`, etc.). This is safe but limiting — users will want:
- `mkdir` — create directories
- `cp`/`mv` — copy/move files
- `pip install` — install packages
- `npm install` — install node packages

**Recommendation:** Don't expand the shell whitelist. Instead, rely on:
1. `FileIOToolsMixin` for file write/create/edit (Phase 1a)
2. `CLIToolsMixin` for broader command execution (Phase 1, with guardrails)
3. Guardrails (#438) to confirm dangerous operations

### 9.4 Guardrails — The Critical Prerequisite

**Issue:** The plan lists guardrails (#438) as "NEXT SPRINT" but also adds
`FileIOToolsMixin` (file write), `TestingMixin` (Python execution), and `CLIToolsMixin`
(arbitrary commands) in the same sprint. This means **write/execute capabilities would
ship before the safety mechanism that protects against them**.

**Mandatory fix:** Guardrails (#438) MUST be implemented BEFORE or simultaneously
with any write/execute capability. The implementation order should be:

```
Week 1: Guardrails framework (#438) + read-only mixins (ProjectManagementMixin)
Week 2: FileIOToolsMixin + ExternalToolsMixin (with guardrails active)
Week 3: CLIToolsMixin + TestingMixin (with guardrails active)
```

### 9.5 Email & Calendar — New Capability Needed

**Completely missing from the plan.** This is a critical gap for a PC agent. Users
expect to triage emails, manage calendar events, and get summaries of their day.

#### MCP Servers Available

| Server | Package | Description |
|--------|---------|-------------|
| **Gmail MCP** | `gmail-mcp-server` / `@anthropic/mcp-gmail` | Read, search, send, label, archive Gmail messages |
| **Outlook MCP** | `outlook-mcp-server` | Microsoft Outlook email access via Graph API |
| **Google Calendar MCP** | `google-calendar-mcp` | Events, scheduling, availability, RSVP |
| **Microsoft Calendar MCP** | `outlook-calendar-mcp` | Outlook calendar via Graph API |
| **Nylas MCP** | `nylas-mcp-server` | Unified email + calendar (Gmail + Outlook + more) |

#### User Workflows (Email)

| # | Workflow | Tools Needed | Validation Test |
|---|---------|-------------|-----------------|
| E1 | "Summarize my unread emails" | Gmail/Outlook MCP → list unread → LLM summarize | User sees bulleted summary of unread emails with sender, subject, key action items |
| E2 | "Find all emails from [person] about [topic]" | Gmail MCP → search → display results | User sees filtered list with relevant messages highlighted |
| E3 | "Draft a reply to [email]" | Gmail MCP → read thread → LLM draft → confirm → send | Draft shown in chat, user confirms, email sent |
| E4 | "Archive/label emails matching [criteria]" | Gmail MCP → search → batch archive/label | Confirmation popup showing N emails to be affected, user approves |
| E5 | "What meetings do I have today?" | Calendar MCP → list events → LLM format | Formatted schedule with times, attendees, locations |
| E6 | "Schedule a meeting with [person] at [time]" | Calendar MCP → check availability → create event | Event created, confirmation shown |
| E7 | "Move my 2pm meeting to 3pm" | Calendar MCP → find event → update → confirm | Confirmation of change, attendees notified |

#### Priority

Add to **Phase 1-MCP** (Tier 2 servers) — these are MCP integrations, not native
code. The ChatAgent just needs `MCPClientMixin` and the user adds the server in
Settings.

### 9.6 Application Control (CUA) & Popular App Demos

The plan covers low-level computer use (Phase 4: pyautogui, pywinauto) but misses
the **high-level application control** pattern that users actually want. Users don't
say "move mouse to (432, 128) and click" — they say "play my Discover Weekly on Spotify"
or "open my latest document in Word".

#### MCP Servers for Application Control

| Server | Package | Description |
|--------|---------|-------------|
| **Spotify MCP** | `spotify-mcp-server` | Play, pause, skip, search, playlist management |
| **Apple Music MCP** | `apple-music-mcp` | Music control on macOS |
| **VS Code MCP** | `vscode-mcp` | Editor control, file management |
| **Obsidian MCP** | `obsidian-mcp-server` | Note-taking, knowledge base |
| **Todoist MCP** | `todoist-mcp-server` | Task management |
| **Linear MCP** | `linear-mcp` | Issue tracking |
| **Discord MCP** | `discord-mcp-server` | Messaging |

#### CUA (Computer Use Agent) Strategy

Two complementary approaches:

1. **MCP-first** (preferred): Use app-specific MCP servers for structured, reliable control.
   Spotify MCP is better than clicking the Spotify UI because it's API-driven, reliable,
   and doesn't break when the UI changes.

2. **Vision + automation** (fallback): For apps without MCP servers, use:
   - Screenshot → VLM (identify UI elements) → pyautogui (click/type)
   - This is Phase 4 in the plan and requires VLM + guardrails

#### Demo Workflows

| # | Workflow | Approach | Validation Test |
|---|---------|----------|-----------------|
| A1 | "Play Discover Weekly on Spotify" | Spotify MCP → search playlist → play | Music starts playing, now-playing info shown in chat |
| A2 | "Open my latest project in VS Code" | Shell (code .) or VS Code MCP | VS Code opens with correct project |
| A3 | "Create a note in Obsidian about today's meeting" | Obsidian MCP → create note | Note created with formatted content |
| A4 | "Take a screenshot and describe what's on screen" | Screenshot tool → VLM analysis | Screenshot shown in chat with description |
| A5 | "Click the submit button on this form" | Screenshot → VLM → pyautogui | Visual confirmation of action |

---

## 10. Detailed Plan Critique & Issues Found

### 10.1 CRITICAL: `FileIOToolsMixin` Has Hidden Dependency

**Plan says:** "Add `FileIOToolsMixin` to ChatAgent — Low effort, just add mixin"
**Reality:** `FileIOToolsMixin` has a **hard dependency** on `ValidationAndParsingMixin`.

From `src/gaia/agents/code/tools/file_io.py` lines 26-31:
```python
class FileIOToolsMixin:
    """...
    NOTE: This mixin expects the agent to also have ValidationAndParsingMixin
    for _validate_python_syntax() and _parse_python_code() methods.
    """
```

When `read_file` processes a `.py` file (line 99), it calls `self._validate_python_syntax(content)`
which is defined in `ValidationAndParsingMixin`, not in `FileIOToolsMixin`. Without it,
reading ANY Python file will crash with `AttributeError`.

**Impact:** Effort is **Medium, not Low**. Options:
1. Add `ValidationAndParsingMixin` to ChatAgent (drags in `CodeSymbol`, `ParsedCode` models, validator classes)
2. Refactor `FileIOToolsMixin` to make Python validation optional (try/except around `_validate_python_syntax`)
3. Create a lightweight `ChatFileIOToolsMixin` that strips out Python-specific features

**Recommendation:** Option 2 — refactor with graceful degradation:
```python
# In read_file, for .py files:
if hasattr(self, '_validate_python_syntax'):
    validation = self._validate_python_syntax(content)
    result["is_valid"] = validation["is_valid"]
else:
    result["file_type"] = "python"  # still tag it, just skip validation
```

### 10.2 CRITICAL: `_TOOL_REGISTRY` Is Global — Tool Count Explosion

**The plan proposes adding 6+ mixins to ChatAgent.** Each mixin registers tools into
`_TOOL_REGISTRY` which is a **module-level global dict** (`src/gaia/agents/base/tools.py:16`).

Current tool counts:
- ChatAgent: **~13 tools** (12 @tool decorators across 3 files)
- CodeAgent: **~57 tools** (69 @tool decorators across 12 files, minus register functions)

If we add `FileIOToolsMixin` (11 tools), `CLIToolsMixin` (6 tools), `ExternalToolsMixin`
(3 tools), `ProjectManagementMixin` (4 tools), `TestingMixin` (3 tools), plus MCP
tools (variable), ChatAgent could have **40+ tools**.

**Problem:** Every tool's full docstring gets appended to the system prompt via
`_format_tools_for_prompt()` (agent.py:370-384). With 40 tools averaging 10 lines of
description each, that's **400+ lines** of tool descriptions in the system prompt.
The default context window is `min_context_size: 32768` tokens (~24K words). Tool
descriptions alone could consume 15-25% of the context.

**Impact:**
- Reduced context for actual conversation history
- LLM confusion from too many tool choices (decision paralysis)
- Slower inference (more tokens to process)

**Recommendations:**
1. **Lazy tool loading**: Only register tools when their category is needed (e.g., don't
   load Prisma tools if user isn't doing database work)
2. **Tool description compression**: Use 1-2 sentence descriptions in prompts, not full docstrings
3. **Tool categories**: Group tools and let the LLM request a category expansion
4. **Per-session tool selection**: Let users enable/disable tool categories from the UI
   (ties into #440 Agent Capabilities Discovery)

### 10.3 HIGH: `ExternalToolsMixin` — Silent Failure Risk

`ExternalToolsMixin` imports from `gaia.mcp.external_services`:
- `get_context7_service()` — requires `npx` on PATH (Node.js installed)
- `get_perplexity_service()` — requires `PERPLEXITY_API_KEY` env var

If neither is available, both tools will return error results but the tools
are **still registered** in the system prompt. The LLM will repeatedly try to use
them and fail.

**Fix:** Conditional tool registration — only register tools if their backend is available:
```python
def register_external_tools(self):
    if shutil.which("npx"):
        # register search_documentation
    if os.environ.get("PERPLEXITY_API_KEY"):
        # register search_web
```

### 10.4 MEDIUM: MCP-vs-Native Build/Buy Confusion

The plan proposes BOTH native implementations AND MCP server equivalents for the
same capabilities:

| Capability | Native (Plan) | MCP Equivalent |
|-----------|--------------|----------------|
| File read/write | FileIOToolsMixin (Phase 1a) | Filesystem MCP (Tier 1) |
| Shell commands | ShellToolsMixin (exists) + CLIToolsMixin (Phase 1) | Desktop Commander MCP (Tier 1) |
| Web search | ExternalToolsMixin (Phase 1d) | Brave Search MCP (Tier 2) |
| Browser | Playwright native (Phase 4g) | Playwright MCP (Tier 1) |
| Git | Shell commands | Git MCP (Tier 1) |

**The plan doesn't resolve which to use.** Having both creates:
- Duplicate tools in registry (LLM sees `read_file` AND `filesystem__read_file`)
- Conflicting behaviors (different error formats, different security models)
- Maintenance burden

**Recommendation:** Clear decision framework:
- **Native tools** for core, always-available capabilities (file I/O, shell)
- **MCP servers** for external integrations and optional capabilities (GitHub, Spotify, email)
- **MCP preferred** when the MCP server is more capable (Playwright MCP > building our own)
- **Never both** for the same capability in the same session

### 10.5 MEDIUM: Missing Effort Estimates & Timeline Reality

The plan says "Phase 1: 1-2 weeks" but doesn't account for:
- Guardrails framework (#438) — design + implement + test = 1 week minimum alone
- `FileIOToolsMixin` refactoring (§10.1) — 2-3 days
- MCP Server Manager UI — new Settings tab + API endpoints + state management = 1 week
- Testing all new tools on Windows + Linux = 3-5 days

**Realistic timeline:**
```
Phase 1 actual: 3-4 weeks (not 1-2)
Phase 1-MCP actual: 2-3 weeks (not concurrent with Phase 1)
Phase 2 actual: 3-4 weeks (VLM model loading is non-trivial)
```

### 10.6 LOW: Cross-Platform Testing Gaps

The plan's cross-platform table (§4) lists tools but doesn't mention:
- **CI/CD**: No GitHub Actions matrix for Windows/Linux/macOS testing
- **pyautogui on headless**: Won't work in CI without virtual display
- **macOS permissions**: Screenshot, accessibility, and automation all require explicit
  System Preferences permissions that can't be automated

---

## 11. User Workflow Validation Tests

Every major capability should have a **user workflow** — a concrete end-to-end
scenario that validates the capability works. These serve as acceptance criteria
and demo scripts.

### 11.1 File Operations Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| F1 | Create a file | "Create a file called hello.py with a hello world program" | File created, content shown in chat | `write_file` |
| F2 | Read & explain | "Read the file main.py and explain what it does" | File content shown, LLM explanation follows | `read_file` |
| F3 | Edit a file | "In config.json, change the port from 3000 to 8080" | Diff shown, file updated, confirmation | `edit_file` with guardrails |
| F4 | Search project | "Find all files that import 'fastapi'" | File list with line numbers | `search_code` |
| F5 | Organize files | "Create a 'docs' folder and move all .md files into it" | Directory created, files moved, summary | `run_shell_command` + guardrails |

### 11.2 Web & Search Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| W1 | Web search | "What are the latest AMD Ryzen AI specs?" | Search results summarized with sources | `search_web` (Brave/Perplexity) |
| W2 | Fetch URL | "Summarize this article: https://example.com/article" | Article content fetched, summarized | Fetch MCP → LLM summarize |
| W3 | Browse website | "Go to github.com/amd/gaia and tell me the latest release" | Page navigated, content extracted | Playwright MCP |
| W4 | Fill web form | "Fill out the contact form on example.com with my info" | Form fields identified, filled, screenshot shown | Playwright MCP + guardrails |

### 11.3 Document Analysis Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| D1 | Index & query | "Index all PDFs in ~/Documents and tell me about project deadlines" | Documents indexed, relevant chunks retrieved, answer synthesized | `index_directory` → `query_documents` |
| D2 | Summarize doc | "Summarize the Q4 report" | Multi-section summary with key findings | `summarize_document` |
| D3 | Compare docs | "Compare these two contracts and highlight differences" | Side-by-side comparison, key differences listed | `query_specific_file` × 2 → LLM compare |
| D4 | Extract data | "Extract all email addresses from this PDF" | Structured list of emails | `dump_document` → LLM extract |

### 11.4 Shell & System Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| S1 | Explore files | "What files are in my project directory?" | File tree displayed | `run_shell_command` (ls/dir) |
| S2 | Git status | "What's the git status of this repo?" | Status, branch, changes shown | `run_shell_command` (git status) |
| S3 | Find large files | "Find all files larger than 100MB on my desktop" | File list with sizes | `run_shell_command` (find/dir) |
| S4 | System info | "What GPU do I have and how much VRAM?" | GPU model, VRAM, driver info | `run_shell_command` (system queries) |

### 11.5 Email & Calendar Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| E1 | Email triage | "Summarize my unread emails" | Bulleted summary: sender, subject, action items | Gmail/Outlook MCP |
| E2 | Email search | "Find emails from Sarah about the budget proposal" | Filtered list with previews | Gmail MCP search |
| E3 | Draft reply | "Draft a polite reply declining the meeting invitation" | Draft shown, user confirms, email sent | Gmail MCP |
| E4 | Calendar check | "What's on my calendar today?" | Formatted schedule with times and details | Calendar MCP |
| E5 | Schedule meeting | "Schedule a 30-min sync with the team at 2pm tomorrow" | Event created, confirmation shown | Calendar MCP |

### 11.6 Browser & App Control Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| B1 | Web lookup | "Look up the Python docs for asyncio.gather" | Browser navigates, content extracted, answer in chat | Playwright MCP |
| B2 | Play music | "Play my Discover Weekly on Spotify" | Spotify starts playing, now-playing shown | Spotify MCP |
| B3 | Screenshot & describe | "Take a screenshot and tell me what's on my screen" | Screenshot captured, VLM description in chat | Screenshot tool + VLM |
| B4 | Open app | "Open VS Code with the gaia project" | VS Code launches with correct folder | `run_shell_command` (code .) |
| B5 | Fill web form | "Go to the HR portal and submit my timesheet" | Browser automation with step-by-step confirmation | Playwright MCP + guardrails |

### 11.7 Guardrails Validation Workflows

| # | Workflow | User Says | Expected Behavior | Tools Used |
|---|---------|-----------|-------------------|------------|
| G1 | File write confirm | "Delete all .tmp files in my project" | Confirmation popup: "Delete 14 .tmp files?" → user approves | Guardrails → shell/file tools |
| G2 | Dangerous command | "Run rm -rf /tmp/old_builds" | Confirmation popup showing exact command, risk level | Guardrails → shell tools |
| G3 | Auto-approve | User clicks "Always allow" for `read_file` | Future `read_file` calls skip confirmation | Guardrails allow-list |
| G4 | Emergency stop | Agent starts doing something unexpected | Kill switch button stops all execution immediately | Cancellation (#439) |
| G5 | Bulk email | "Send this email to everyone in my contacts" | Hard block: "Bulk email operations require explicit approval for each recipient" | Guardrails escalation |

---

## 12. Updated MCP Server Catalog (Complete)

Adding the missing Tier 2+ servers identified in the critique:

### Tier 2+ — Communication & Productivity (NEW)

| Server | Package | Description | Category |
|--------|---------|-------------|----------|
| **Gmail** | `gmail-mcp-server` | Email read, search, send, label, archive | Email |
| **Outlook** | `outlook-mcp-server` | Microsoft email via Graph API | Email |
| **Google Calendar** | `google-calendar-mcp` | Events, scheduling, availability | Calendar |
| **Outlook Calendar** | `outlook-calendar-mcp` | Microsoft calendar via Graph API | Calendar |
| **Nylas** | `nylas-mcp-server` | Unified email + calendar (multi-provider) | Email+Calendar |
| **Spotify** | `spotify-mcp-server` | Music playback, search, playlists | App Control |
| **Todoist** | `todoist-mcp-server` | Task management, projects, labels | Productivity |
| **Obsidian** | `obsidian-mcp-server` | Note-taking, knowledge base | Productivity |
| **Linear** | `linear-mcp` | Issue tracking, project management | Dev Tools |
| **Discord** | `discord-mcp-server` | Messaging, channel management | Communication |

---

## 13. New SDK Capabilities: MCP Auto-Discovery & SKILL.md Support

### 13.1 MCP Server Auto-Discovery & Installation

**Problem:** When a user asks the agent to do something it can't (e.g., "check my email"),
the agent currently says "I can't do that." A modern agent should be able to **find,
recommend, and install** the right MCP server to gain the capability.

**Design:**

```
User: "Check my email for anything urgent"
Agent: I don't have email access yet. I found these MCP servers that can help:
       1. Gmail MCP (gmail-mcp-server) — Gmail access
       2. Outlook MCP (outlook-mcp-server) — Outlook/Microsoft 365
       3. Nylas MCP (nylas-mcp-server) — Multi-provider (Gmail + Outlook + more)
       Would you like me to install one?
User: "Install Gmail MCP"
Agent: [installs via npx, prompts for OAuth/credentials, connects]
       Gmail MCP is now connected. You have 3 urgent emails...
```

**Implementation:**

| Component | Description | Milestone |
|-----------|-------------|-----------|
| **MCP Registry Client** | Query public MCP registries (npmjs.com, mcpservers.org, GitHub) to find servers by capability keyword | **B** (new SDK) |
| **Capability-to-MCP Mapper** | Map user intent ("email", "calendar", "spotify") to known MCP server packages | **A** (config/catalog, curated list) |
| **Auto-Install Flow** | `npx -y <package>` with user confirmation, credential prompting, connection test | **B** (new SDK) |
| **Fallback Search** | If curated catalog doesn't match, search npm/GitHub for `mcp-server-*` packages | **B** (new SDK) |
| **UI: Install Prompt** | Agent UI shows "Install MCP server?" card with description, permissions, confirm button | **A** (UI) |

**Curated Capability Map** (ships with GAIA):
```json
{
  "capabilities": {
    "email": ["gmail-mcp-server", "outlook-mcp-server", "nylas-mcp-server"],
    "calendar": ["google-calendar-mcp", "outlook-calendar-mcp"],
    "browser": ["@anthropic/mcp-playwright"],
    "web_search": ["@anthropic/mcp-brave-search"],
    "music": ["spotify-mcp-server"],
    "notes": ["obsidian-mcp-server"],
    "tasks": ["todoist-mcp-server"],
    "code": ["@modelcontextprotocol/server-github", "@modelcontextprotocol/server-git"],
    "files": ["@modelcontextprotocol/server-filesystem"],
    "database": ["@modelcontextprotocol/server-postgres"]
  }
}
```

### 13.2 Anthropic SKILL.md Support for GAIA Agent SDK

**What is SKILL.md?** Anthropic's specification for agents to document their learned
skills — reusable procedures, workflows, and domain knowledge that persist across
sessions. Skills are stored as markdown files that the agent can read, update, and
reference.

**Why it matters:** GAIA agents should be able to learn from experience. If a user
teaches the agent a multi-step workflow ("here's how to deploy our app"), that knowledge
should persist and be reusable.

**Design for GAIA:**

| Component | Description | Milestone |
|-----------|-------------|-----------|
| **Skills Directory** | `~/.gaia/skills/` directory for storing skill files | **B** |
| **Skill Loader** | At agent startup, load all `*.md` files from skills dir into context | **B** |
| **Skill Writer** | Tool: `save_skill(name, content)` — agent can persist learned workflows | **B** |
| **Skill Search** | Tool: `search_skills(query)` — find relevant skills for current task | **B** |
| **Skill Format** | Follow Anthropic's SKILL.md format: title, description, steps, prerequisites | **B** |
| **Skill UI** | Skills panel in Agent UI Settings — view, edit, delete, import/export skills | **A** (UI) |
| **Skill Sharing** | Export skills as `.md` files, import from community/team repositories | **B** |

**SKILL.md Format (Anthropic-compatible):**
```markdown
# Deploy GAIA Application

## Description
Steps to deploy the GAIA application to production.

## Prerequisites
- Docker installed
- Access to container registry
- `.env.production` file configured

## Steps
1. Run tests: `pytest tests/ -x`
2. Build Docker image: `docker build -t gaia:latest .`
3. Push to registry: `docker push registry.example.com/gaia:latest`
4. Deploy: `kubectl apply -f k8s/deployment.yaml`

## Learned
- Always run tests before building (learned 2026-03-01)
- Use `--no-cache` flag if dependencies changed (learned 2026-03-05)
```

**Integration with existing GAIA features:**
- Skills can reference RAG documents ("See indexed doc: architecture.pdf")
- Skills can reference MCP servers ("Requires: gmail-mcp-server")
- Skills can include tool sequences that the agent replays
- Skills directory is auto-indexed by RAG for semantic search

---

## 14. Summary: Recommended Priority Order (Revised, Split by Milestone)

### Milestone A — Agent UI: Wire Existing SDK (Weeks 1-6)

```
IMMEDIATE (This branch — kalin/agent-ui)
  ├── ✅ Windows shell compatibility fix (done)
  ├── ✅ Sidebar minimize + resize (done)
  └── ✅ Milestone + issues created (#438-#442)

WEEK 1-2: Foundation + MCP Framework
  ├── Add MCPClientMixin to ChatAgent
  ├── MCP Server Manager UI panel in Settings
  ├── Curated MCP server catalog (Tier 1: Playwright, Brave, Fetch, Filesystem, Git)
  ├── Refactor FileIOToolsMixin for graceful degradation (§10.1)
  ├── Conditional tool registration for ExternalToolsMixin (§10.3)
  └── Capability-to-MCP mapper (curated catalog — §13.1)

WEEK 3-4: Wire Existing Mixins + MCP Tier 1
  ├── Add FileIOToolsMixin to ChatAgent (file read/write/edit)
  ├── Add ProjectManagementMixin (list_files)
  ├── Add ExternalToolsMixin (web search, conditional)
  ├── Enable Playwright MCP (browser control)
  ├── Enable Brave Search MCP (web search, free)
  ├── Enable Fetch MCP (URL content extraction)
  └── Agent capabilities discovery API (#440)

WEEK 5-6: MCP Tier 2 + Productivity
  ├── MCP Tier 2 servers: Gmail, Outlook, Calendar, Spotify, Obsidian
  ├── MCP install prompt UI ("Install MCP server?" card)
  ├── Skills UI panel in Settings (view/manage SKILL.md files)
  ├── Tool argument streaming (#441)
  └── Per-session MCP server enable/disable
```

### Milestone B — GAIA Agent SDK: New Capabilities (Weeks 3-12+)

```
WEEK 3-5: Guardrails + Safety (PARALLEL with Milestone A)
  ├── Tool execution guardrails framework (#438) ← MUST BE FIRST
  │   ├── OutputHandler.confirm_tool_execution() API
  │   ├── SSE handler → frontend confirmation modal
  │   ├── threading.Event blocking pattern
  │   ├── Allow-list with localStorage persistence
  │   └── Risk classification (read/write/execute/destructive)
  ├── Cooperative execution cancellation (#439)
  └── Cross-platform shell compatibility (#442)

WEEK 5-7: Vision & Media
  ├── ScreenshotToolsMixin — cross-platform (PIL.ImageGrab, mss)
  ├── Wire VLMToolsMixin into ChatAgent (image analysis)
  ├── Image display in Agent UI (base64/file URL)
  ├── Screenshot → VLM → describe workflow
  └── Wire SDToolsMixin (image generation, optional)

WEEK 7-9: SDK Architecture
  ├── Tool categories + lazy loading (§10.2)
  ├── Tool description compression for prompts
  ├── MCP auto-discovery (search npm/GitHub for servers) — §13.1
  ├── SKILL.md support (load, save, search, format) — §13.2
  └── Skill-RAG integration (auto-index skills directory)

WEEK 9-12: Computer Use (CUA)
  ├── Windows Desktop MCP integration
  ├── Mouse/keyboard control (pyautogui) with mandatory guardrails
  ├── Window management (pywinauto/wmctrl)
  ├── VLM-based screen element detection
  └── CUA demo workflows (open apps, fill forms)

LATER: Audio/Voice
  ├── Voice input (Whisper ASR in Agent UI)
  ├── Voice output (Kokoro TTS in Agent UI)
  └── Continuous voice conversation mode
```

### Milestone Dependency Map

```
Milestone A (UI/Wiring)          Milestone B (SDK)
═══════════════════════          ═════════════════
Week 1: MCPClientMixin
Week 2: MCP Server UI            Week 3: Guardrails (#438) ← blocks write tools
Week 3: FileIOToolsMixin ───────────────→ needs guardrails for write ops
Week 4: Playwright MCP            Week 4: Cancellation (#439)
Week 5: Email/Calendar MCP        Week 5: ScreenshotToolsMixin
Week 6: Skills UI ────────────────Week 6: SKILL.md SDK support
                                  Week 7: Tool categories
                                  Week 8: MCP auto-discovery
                                  Week 9-12: Computer Use
```

**Key dependency:** `FileIOToolsMixin` (Milestone A, week 3) needs guardrails
(Milestone B, week 3) to be safe for write operations. These should be developed
in parallel with guardrails landing first or simultaneously.
