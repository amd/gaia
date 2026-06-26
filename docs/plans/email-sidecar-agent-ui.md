# Design: Email Agent in the Agent UI — dual user/dev modes

**Date:** 2026-06-26
**Status:** Draft (v2 — topology corrected after code review) — pending user review
**Related:** epic #1767 (Email in the Agent UI), #1768 (`/v1/email/*` REST surface),
npm pkg `@amd-gaia/agent-email` (`hub/agents/npm/agent-email`), Python agent
`hub/agents/python/email`

## Problem

We want the GAIA Agent UI to triage the user's **personal Gmail**, with two things
true at once:

1. **Users** get a stable, no-Python experience — the agent ships as the frozen
   `@amd-gaia/agent-email` sidecar binary, with no heavy email deps in the core
   backend.
2. **Developers** can improve the agent and see changes **live**, without the
   freeze → `npm publish` → version-bump → re-integrate patch loop that makes
   iteration painful today.

## How the UI actually wires email today (verified)

This is the ground truth the design must respect — an earlier draft got it wrong.

- The frontend has **no email REST client**. It surfaces email triage through the
  **chat pipeline**: the chat agent calls a tool (`pre_scan_inbox`), the result is
  emitted as a structured payload on the chat SSE stream
  (`src/gaia/ui/sse_handler.py:472`), and the renderer draws it as
  `EmailPreScanCard` inside `MessageBubble` (`MessageBubble.tsx:727`).
- The email agent runs **in-process** in the Python UI backend: `_chat_helpers.py`
  constructs it via the session agent factory (`:1300`, `:1776`, passing
  `mail_provider`) and runs it through the chat loop.
- `src/gaia/ui/server.py:599-601` *also* mounts `gaia_agent_email`'s `/v1/email/*`
  router in-process — but that is a **separate external/programmatic surface**
  (#1768), not what the UI's own rendering uses.
- The agent reads the user's mailbox itself via the connectors framework, using the
  Google grant at `~/.gaia/connectors/grants.json` (`src/gaia/connectors/grants.py:53`).

**Consequences for the design:**
- The seam for user/dev mode is the **chat agent's email-tool layer in the Python
  backend** — not the renderer, and not Electron main.
- The lifecycle host is the **Python UI backend**, which already owns the chat loop
  and the grant. **No Node.js backend is required** — the frozen sidecar is a
  self-contained HTTP executable the Python backend can spawn and call directly.
  (The npm package remains the integration path for *external* Node/Electron apps;
  it is just not GAIA's own consumption path.)

## Key insight — one REST seam, two interchangeable backends

The frozen binary is PyInstaller wrapping `packaging/server.py`'s `build_app()`, so
the binary and the raw Python source serve a **byte-identical** contract at
`http://127.0.0.1:8131/v1/email/*`. Anything answering that URL is interchangeable.

Because the agent owns its own mailbox connection and reads the shared
`~/.gaia/connectors/grants.json`, **personal Gmail works identically in both
modes**. The user authenticates Google once; the mode only swaps *which process*
answers the REST calls.

## Architecture

```
Agent UI renderer  (UNCHANGED — renders email_pre_scan cards from chat SSE)
   │  chat SSE
   ▼
Python UI backend  (port 4200 — owns chat loop, SSE, grant)
   │  chat agent calls email tools
   ▼
Email proxy tools  (thin HTTP shims, in the chat agent's tool layer — NEW)
   │  POST http://127.0.0.1:8131/v1/email/*
   ▼
EmailSidecarManager  (Python, in the UI backend — NEW)
   │  reads GAIA_EMAIL_AGENT_MODE
   ├─ user mode → spawn frozen binary    (lazy-fetched to ~/.gaia cache)
   └─ dev  mode → spawn uvicorn --reload  (local Python source)
        │
        ▼  both serve the identical contract
   sidecar :8131  ──reads──> ~/.gaia/connectors/grants.json ──> user's Gmail
```

The renderer and the SSE card pipeline are untouched: the proxy tools return the
same `pre_scan_inbox` envelope the in-process agent returns today, so
`sse_handler.py` and `EmailPreScanCard` keep working unchanged.

**Both modes go through the same HTTP path** — only the *served process* differs.
This is deliberate: it avoids the "works in dev, breaks for users" masking failure
where an in-process dev path and an out-of-process prod path diverge. Process
isolation (the thing #1767 cares about) is then identical in both modes.

### Components

| Unit | Home | Responsibility | Depends on |
|------|------|----------------|------------|
| `EmailSidecarManager` | `src/gaia/ui/` (Python backend) | Pick mode, spawn the right process, wait for `/health`, expose base URL, tree-kill on shutdown | `subprocess`, the frozen binary / uvicorn |
| Email proxy tools | chat agent tool layer | Forward `pre_scan_inbox`/triage/draft to the sidecar; return the existing envelope | `EmailSidecarManager` base URL |
| Mode config | `GAIA_EMAIL_AGENT_MODE` env (`user` default / `dev`) | Select backend | — |

### User mode (default)
Lazy-fetch the frozen binary into a **`~/.gaia/agents/email/` cache** on first run
(R2 + lock-file verified) → spawn → poll `GET /health` → tree-kill on backend exit
(`taskkill /F /T` on Windows; detached process-group kill on POSIX). Cache means
offline after first fetch and no installer/build-pipeline change. A failed fetch
fails loudly (no fallback to dev mode). The core backend ships **without** the
email agent's Python deps.

### Dev mode
Spawn uvicorn's **CLI with an import string** so hot-reload works:

```
python -m uvicorn packaging.server:app --reload --host 127.0.0.1 --port 8131
# cwd = hub/agents/python/email   (app = build_app() exists at module scope)
```

> `packaging/server.py`'s `__main__` calls `uvicorn.run(app, …)` with the *app
> object*, which cannot hot-reload — dev mode must use the import-string CLI form
> above (no change to `server.py` required).

Edit any `.py` / prompt / tool → uvicorn reloads in ~1s → the next chat action hits
new code. **No freeze, no publish, no version bump, no UI rebuild.** Dev mode
assumes the source env is set up; if uvicorn or the package can't be imported, fail
loudly with `uv pip install -e hub/agents/python/email` — no silent auto-install,
no fallback to user mode. Dev mode only applies to a source checkout (the packaged
app has no Python source).

## Data flow (both modes, identical)
1. User asks the chat agent to triage (e.g. "what's in my inbox").
2. Chat agent calls the `pre_scan_inbox` proxy tool.
3. Tool POSTs to `http://127.0.0.1:8131/v1/email/*`.
4. Sidecar (binary or uvicorn) reads the `~/.gaia` grant, calls Gmail, returns the
   envelope.
5. Tool returns the envelope unchanged → `sse_handler` injects it → renderer draws
   the `EmailPreScanCard`.

## Error handling (fail loudly — no silent fallbacks)
- Binary missing/un-fetchable in user mode → surface `BinaryNotFoundError` with its
  actionable fetch hint; never silently fall back to dev mode.
- `/health` not ready within timeout → loud error with the sidecar's last stderr.
- Lemonade unreachable → sidecar already returns HTTP 502; surface verbatim.
- Dev mode requested but source/uvicorn missing → loud startup error naming the
  expected path + the `uv pip install -e` remedy.
- Port 8131 in use → fail with the conflicting-process hint. **Never touch 4001.**

## Testing
- **Unit:** `EmailSidecarManager` mode selection + spawn-arg *shape* (assert dev mode
  uses the `--reload` import-string form; user mode uses the cached binary path),
  health-poll, tree-kill on shutdown.
- **Integration:** with a running sidecar (both modes), `GET /health` then a real
  `pre_scan_inbox` round-trip through the proxy tool — proves the *call is valid*,
  not just invoked.
- **Cold-start:** confirm the frozen binary bundles `gaia.connectors` so user mode
  reads the grant — verify from an empty-state machine, not a warm box.
- **Card pipeline:** assert the proxy tool's envelope still triggers the
  `email_pre_scan` SSE injection and `EmailPreScanCard` render.
- **Prompt/LLM changes** still require `gaia eval agent` (email category) vs.
  baseline before "done" — iteration speed ≠ skipping verification.

## Resolved decisions
1. **Host:** the Python UI backend, not a new Node backend and not Electron main —
   that's where the chat loop, the grant, and the existing in-process integration
   already live. (Electron main *could* optionally own just the sidecar *process*
   supervision since it already manages subprocesses, but splitting spawn from
   routing adds coordination/race complexity for no clear gain; keep both in the
   backend.)
2. **Faithful modes:** both modes use the same HTTP-proxy path; only the served
   process differs (binary vs uvicorn-source). No in-process dev shortcut, to avoid
   dev/prod divergence.
3. **Frozen binary on disk:** lazy-fetched to a `~/.gaia/agents/email/` cache; loud
   failure on fetch error.
4. **Dev env:** assume ready; fail loud with `uv pip install -e …` otherwise.

## Scope / YAGNI
- **In:** `EmailSidecarManager`, mode flag, both spawn paths, the proxy tool shims,
  health + shutdown, tests, a short dev-mode doc.
- **Out (follow-ups):** Settings-UI toggle (env flag is enough now); generalizing
  the manager to other hub agents; Outlook/multi-mailbox specifics (the agent owns
  those).

## Relationship to epic #1767
#1767 rips out the *in-process* email agent. Concretely this design **replaces** the
in-process agent factory path in `_chat_helpers.py` (and the in-process
`/v1/email/*` mount in `server.py:599-601`) with: (a) the sidecar-spawning
`EmailSidecarManager`, and (b) email proxy tools the chat agent calls. The two
efforts should land together so the UI ends with exactly one email backend — the
out-of-process sidecar. Sequencing/ownership to be coordinated with @itomek (PR
#1785).

## Open question for reviewer
- Should the chat agent's email capability be a single "proxy agent" that maps all
  its tools to sidecar calls, or a small set of proxy tools composed onto the
  existing chat agent? (Affects how `agent_type=email` sessions are constructed in
  `_chat_helpers.py`.)
