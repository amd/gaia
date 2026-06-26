# Design: Email Agent in the Agent UI — frozen sidecar + dual user/dev modes

**Date:** 2026-06-26
**Status:** Draft (v3.1 — sidecar direction, bulletproofed) — pending user review
**Decision (2026-06-26):** The Agent UI's email backend is the **out-of-process
frozen sidecar** (the published Hub product), lazily downloaded on demand. Dev mode
runs the same contract from local Python source for fast iteration. This **reopens
the in-process decision in epic #1767** — see "Relationship to #1767" — and needs
maintainer + @itomek sign-off before build.
**Related:** #1767 (epic), #1768 (`/v1/email/*` REST surface), #1778/#1779/#1780/#1781
(REST capability build-out), npm pkg `@amd-gaia/agent-email`, Python agent
`hub/agents/python/email`

## Problem

Triage the user's **personal Gmail** through the Agent UI, with:
1. **Users** — stable, no-Python: the agent runs as the frozen sidecar; the core
   backend ships without heavy email deps, and the install stays **lightweight** —
   only the email sidecar is downloaded, on demand.
2. **Developers** — improve the agent and see changes **live**, no
   freeze → `npm publish` → version-bump → re-integrate patch loop.

## Why a sidecar (not the simpler in-process editable install)

A code review fairly asked: an editable install (`uv pip install -e`) already gives
in-process hot-iteration — why add a sidecar? Three reasons the sidecar earns its
place, beyond iteration speed:

1. **It validates the shipped product.** User mode runs the *exact* frozen binary
   published to the Agent Hub (pinned via `binaries.lock.json`). The Agent UI
   becomes a first-class consumer of the real artifact — so product regressions
   surface in our own app, not just in a downstream integrator's. Editable-install
   never exercises the thing customers actually run.
2. **It keeps the core backend lightweight.** The email agent's heavy deps stay out
   of the core wheel/installer; the sidecar is fetched **only when email is used**,
   and **only the one platform binary** is downloaded — nothing else.
3. **Crash isolation.** A fault in email tooling can't take down the chat backend.

Dev mode (uvicorn-from-source) and user mode (frozen binary) therefore serve two
distinct purposes — **fast iteration** and **product validation** — over one shared
REST contract.

## Ground truth: how the UI wires email today (verified)

- The frontend has **no email REST client**. Email triage is surfaced via the
  **chat pipeline**: the chat agent calls a tool (`pre_scan_inbox`), the result is
  emitted on the chat SSE stream (`sse_handler.py:472`), and the renderer draws
  `EmailPreScanCard` in `MessageBubble` (`MessageBubble.tsx:727`).
- The agent runs **in-process** in the Python backend via the session agent factory
  (`_chat_helpers.py:1300,1776`, passing `mail_provider`); `agent_type=email`
  selects it.
- `server.py:599-601` mounts `gaia_agent_email`'s `/v1/email/*` router in-process —
  a separate external/programmatic surface (#1768), not what the UI renders from.
- The agent reads the mailbox itself via connectors, using the Google grant at
  `~/.gaia/connectors/grants.json` (`grants.py:53`); OAuth secrets resolve via the
  OS keyring, not the JSON ledger.

## ⚠️ Precondition: the REST contract is not yet sufficient for the UI

The sidecar's contract **today** (verified) is:

| UI capability | REST route today | Status |
|---|---|---|
| triage a pasted email/thread | `POST /v1/email/triage` | ✅ exists |
| draft / send | `POST /v1/email/draft` · `/send` | ✅ exists |
| health / version / spec / playground | `GET /health` `/version` `/spec` `/playground` | ✅ exists |
| connector configure/complete/list/remove | `/v1/email/connectors/*` | ✅ exists |
| **inbox pre-scan** (`email_pre_scan` card) | — | ❌ in flight (pre-scan REST) |
| **inbox search** | — | ❌ #1781 |
| **archive / quarantine** | — | ❌ #1779 |
| **calendar** | — | ❌ #1780 |

Critically, `/triage` takes the email **in the request body** — it does not scan the
mailbox. The UI's headline feature, the `email_pre_scan` card, is produced **only**
by the agent-loop tool that scans the inbox (`agent.py:663-717`, `read_tools.py:572`).
So the **sidecar cannot serve the UI's inbox features until those become REST
routes** — work that is in flight (#1779/#1780/#1781 + inbox pre-scan REST). **This
design is sequenced behind them.** Each new route is a contract change governed by
the npm version guard (`lifecycle.ts:checkVersion`).

## Key seam — one REST contract, two interchangeable backends

The frozen binary is PyInstaller wrapping `packaging/server.py`'s `build_app()`, so
once the routes above exist, the **binary and the raw Python source serve an
identical contract** at `http://127.0.0.1:<sidecar-port>/v1/email/*`. The agent owns
its mailbox connection and reads the shared `~/.gaia/connectors/grants.json`, so
**personal Gmail works identically in both modes** — the mode only swaps which
*process* answers the calls.

## Architecture

```
Agent UI renderer  (UNCHANGED — renders email_pre_scan cards from chat SSE)
   │  chat SSE
   ▼
Python UI backend  (port 4200 — owns chat loop, SSE, connector OAuth, grant writes)
   │  agent_type=email → Email proxy agent
   ▼
Email proxy agent + tools  (chat agent tool layer — NEW; replaces in-process agent)
   │  POST http://127.0.0.1:<sidecar-port>/v1/email/*
   ▼
EmailSidecarManager  (Python, in the UI backend — NEW; spawn/health/shutdown/port)
   │  reads GAIA_EMAIL_AGENT_MODE
   ├─ user mode → frozen binary   (lazy-fetched on first email use via npm pkg CLI)
   └─ dev  mode → uvicorn --reload (local Python source)
        │
        ▼  both serve the identical contract
   sidecar  ──reads (no writes)──> ~/.gaia/connectors/grants.json ──> user's Gmail
```

The renderer and SSE card pipeline are untouched: the proxy agent's tools return the
same envelopes (e.g. `pre_scan_inbox`), so `sse_handler.py` and `EmailPreScanCard`
keep working once the pre-scan REST route exists.

**Both modes use the same HTTP path** — only the served process differs. Production
is now out-of-process too, so dev and prod share the same isolation topology (no
in-process/out-of-process divergence).

### Components

| Unit | Home | Responsibility |
|------|------|----------------|
| `EmailSidecarManager` | `src/gaia/ui/` (Python backend) | mode select; lazy-spawn binary/uvicorn on first email use; health-poll; tree-kill on shutdown; own an ephemeral per-instance port |
| Email proxy agent + tools | chat agent tool layer (`agent_type=email`) | forward triage/draft/send/pre-scan/etc. to the sidecar; return the existing envelopes unchanged |
| Binary fetch | **npm pkg CLI** (`npx @amd-gaia/agent-email fetch`) as a subprocess | keep the SHA-256/lock-file integrity check in its canonical TS impl (`fetch.ts`) — **do not re-implement the security boundary in Python** |
| Mode config | `GAIA_EMAIL_AGENT_MODE` env (`user` default / `dev`) | select backend |

## Resolved design decisions

1. **Proxy *agent*, not loose tools.** Reuse the existing `agent_type=email` session
   machinery: replace the in-process `EmailTriageAgent` constructed in
   `_chat_helpers.py` with a thin **proxy agent** whose tools call the sidecar and
   return the same envelopes. This preserves session construction, `mail_provider`
   plumbing, and the SSE card path with the smallest diff.
2. **Lightweight, lazy, scoped download.** The sidecar is fetched **on first email
   use** (not at app startup), **only the current platform's binary**, into a
   `~/.gaia/agents/email/` cache (cache-hit skips re-download; offline thereafter).
   The core install bundles **no** email sidecar. This establishes a reusable
   *lazy-per-agent-sidecar* pattern, but scope here is **email only** (YAGNI).
3. **User mode pins the published artifact.** Fetch verifies against the shipped
   `binaries.lock.json`, so the UI exercises the exact Hub-published binary
   (dogfooding). A failed/integrity-mismatched fetch fails loudly — never falls back
   to dev mode or to in-process.
4. **The sidecar is the single `/v1/email` surface; remove the in-process mount.**
   The in-process `server.py:599-601` mount (#1768) becomes redundant once the UI is
   sidecar-only and contradicts the lightweight-core goal (it imports the email
   wheel into core). Remove it from core; the sidecar itself serves the external
   `/v1/email` surface. Coordinate the removal with the #1768 owner.

### Dev mode enabler (one small, owned change)
Hot-reload needs uvicorn's import-string form, which needs a **module-level app**
that does not exist today (it lives inside `build_app()`/`main()`):

```python
# hub/agents/python/email/packaging/server.py — add at module scope:
app = build_app()        # build_app also mounts /v1/email/connectors/* — fine for dev
```
```bash
python -m uvicorn packaging.server:app --reload --host 127.0.0.1 --port <port>
```
Edit any `.py` / prompt / tool → uvicorn reloads in ~1s → next chat action hits new
code. Dev mode assumes the source env is set up; if uvicorn/the package can't be
imported, fail loudly with `uv pip install -e hub/agents/python/email` — no silent
auto-install, no fallback. Dev mode is source-checkout only.

## Lifecycle, port & ownership (review fixes)
- **Reuse, don't reinvent:** `lifecycle.ts`/`fetch.ts` already implement
  SHA-verified fetch, tree-kill, health-poll, version-check, auto-reap. User-mode
  fetch goes through the npm CLI; Python owns only spawn + health + tree-kill (not a
  security boundary).
- **Port:** a fixed `8131` breaks two concurrent `gaia chat --ui` instances. The
  manager binds an **ephemeral port per backend instance** and passes it to the
  proxy agent. **Never 4001.**
- **One backend for the UI:** the UI talks only to the sidecar (decision 4).

## Auth & grants (review fixes)
- **Single writer:** all connector OAuth flows stay on the **Python backend**; the
  sidecar **reads** the grant + resolves keyring secrets but does **not** run OAuth
  writes (its `/connectors/{provider}/complete` write route is **not** exposed to
  the UI). This avoids cross-process writes to `grants.json`, whose concurrency
  guard is per-process only (`grants.py:56-61`).
- **Bundling verified:** `freeze.py:119` collects `gaia.connectors`, so the binary
  can read the grant. Cold-start test must assert a **real keyring token resolve**
  from the frozen binary, not merely that `gaia.connectors` imports.

## Error handling (fail loudly)
- Binary missing/integrity-fail in user mode → loud error with the fetch remedy;
  never fall back to dev/in-process.
- `/health` timeout → loud error with the sidecar's last stderr.
- Lemonade unreachable → sidecar returns HTTP 502; surface verbatim.
- Dev mode without source/uvicorn → loud error naming the path + `uv pip install -e`.
- Sidecar port in use → fail with the conflicting-process hint.

## Testing
- **Unit:** mode selection + spawn-arg *shape* (dev → `--reload` import-string;
  user → cached binary path); health-poll; tree-kill on shutdown; ephemeral-port
  wiring to the proxy agent; lazy-fetch only on first email use.
- **Integration:** with a running sidecar (both modes) `GET /health` then a real
  inbox pre-scan round-trip through the proxy agent — proves the *call is valid*.
- **Dogfood/product check:** user mode launches the binary resolved from
  `binaries.lock.json` and a smoke triage succeeds — proves the *shipped* artifact
  works end-to-end in the UI.
- **Cold-start:** frozen binary resolves a real keyring token from an empty-state
  machine (not a warm box).
- **Card pipeline:** the proxy agent's envelope still triggers `email_pre_scan` SSE
  injection + `EmailPreScanCard` render.
- **Prompt/LLM changes:** `gaia eval agent` (email category) vs. baseline before
  "done."

## Sequencing
1. **Blocked on** the REST capability build-out (inbox pre-scan + #1779/#1780/#1781)
   — the sidecar can't serve the UI's inbox features until those routes exist.
2. Land the dev-mode enabler (`app = build_app()` at module scope).
3. Build `EmailSidecarManager` + the email proxy agent; wire user/dev modes + lazy
   on-demand fetch.
4. Switch `agent_type=email` sessions from the in-process agent to the proxy agent
   (`_chat_helpers.py`); remove the in-process #1768 mount (with #1768 owner).
5. Tests (incl. the dogfood/product check) + a short dev-mode doc.

## Relationship to epic #1767 (must reconcile before build)
#1767 **chose the in-process router mount** and **explicitly rejected the
frozen-binary sidecar for the UI**; its capstone PR #1785 was **closed unmerged on
2026-06-26**, so the in-process path is abandoned in code but the *decision* stands
on paper. This plan **pivots to the sidecar**, justified by product validation +
lightweight core + isolation (above). Its variant also answers #1767's stated
objection — #1767 rejected the sidecar as "needs a Node host / breaks browser mode,"
but here the **Python backend** spawns it, so it works in browser mode with no Node
host. **Action: get maintainer + @itomek sign-off to supersede #1767's in-process
decision before implementation.**

## Remaining decision for sign-off
- Confirm removal of the #1768 in-process `/v1/email` external mount once the UI is
  sidecar-only (decision 4), vs. keeping it as a distinct external surface. This is
  the only open architectural choice; everything else is resolved above.
