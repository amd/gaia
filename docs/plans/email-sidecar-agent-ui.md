# Design: Email Agent in the Agent UI — dual user/dev sidecar modes

**Date:** 2026-06-26
**Status:** Draft — pending user review
**Related:** epic #1767 (Email in the Agent UI), npm pkg `@amd-gaia/agent-email` (`hub/agents/npm/agent-email`), Python agent `hub/agents/python/email`

## Problem

We want the GAIA Agent UI to triage the user's **personal Gmail** through the email
agent. Two things must be true at once:

1. **Users** get a stable, no-Python experience — the agent ships as the frozen
   `@amd-gaia/agent-email` npm sidecar binary.
2. **Developers** can improve the agent and see changes **live**, without the
   freeze → `npm publish` → version-bump → re-integrate patch loop that makes
   iteration painful today.

## Key insight — one REST seam, two interchangeable backends

`EmailClient` (and therefore the whole UI) only ever speaks HTTP to a fixed
address: `http://127.0.0.1:8131/v1/email/*`. The frozen npm binary is just
PyInstaller wrapping `hub/agents/python/email/packaging/server.py`, so **the
binary and the raw Python source serve a byte-identical contract**. Anything that
answers that URL is interchangeable.

The agent owns its own Gmail connection — it reads the inbox itself via the
connectors framework (`get_credential_sync`, `list_inbox`, `triage_inbox`,
`pre_scan_inbox`) using the Google OAuth grant stored locally in `~/.gaia`. The UI
never passes raw email payloads. Consequence: **personal Gmail works identically
in both modes**, because both run the same agent code against the same local grant.
The user authenticates Google once; the mode only swaps which *process* answers the
REST calls.

## Architecture

```
Agent UI renderer
   │  (UNCHANGED — calls one fixed base URL: http://127.0.0.1:8131)
   ▼
EmailSidecarManager   (Electron main process, Node — NEW)
   │  reads GAIA_EMAIL_AGENT_MODE
   ├─ user mode → spawn frozen binary   (npm pkg: resolveBinaryPath → spawn → health-wait → tree-kill)
   └─ dev  mode → spawn uvicorn --reload (local Python source)
        │
        ▼  both serve the identical REST contract
   http://127.0.0.1:8131/v1/email/*  ──reads──> ~/.gaia Google grant ──> user's Gmail
```

### Components

| Unit | Home | Responsibility | Depends on |
|------|------|----------------|------------|
| `EmailSidecarManager` | `src/gaia/apps/webui/services/email-sidecar-manager.cjs` (Electron main) | Pick mode, spawn the right process, wait for `/health`, expose status + base URL to the renderer, tree-kill on shutdown | npm pkg lifecycle helpers (`lifecycle.ts`), child_process |
| Renderer email client | existing UI services | Call `/v1/email/*` at the manager-provided base URL | base URL only — **no mode awareness** |
| Mode config | `GAIA_EMAIL_AGENT_MODE` env (`user` default / `dev`); optional `emailAgentMode` key in `~/.gaia/tray-config.json` | Select backend | — |

The manager mirrors the existing `AgentProcessManager` patterns (spawn, health
check, graceful-then-force shutdown, crash logging) but over **HTTP**, not stdio
JSON-RPC.

### User mode (default)
Reuse the npm package's Node lifecycle helpers verbatim:
`fetchBinary({ outDir })` (guarded by `binaryExists`) lazily downloads the frozen
binary into a **`~/.gaia/agents/email/` cache** on first run (R2 + lock-file
verified) → `resolveBinaryPath({ resourcesDir })` → `spawn` → poll `GET /health`
→ on app exit tree-kill (`taskkill /F /T` on Windows; detached process-group kill
on POSIX). Cache means offline after the first fetch and no installer/build-pipeline
change. A failed fetch fails loudly (no fallback to dev mode).

### Dev mode
Spawn uvicorn's **CLI with an import string** so hot-reload works:

```
python -m uvicorn packaging.server:app --reload --host 127.0.0.1 --port 8131
# cwd = hub/agents/python/email   (app = build_app() exists at module scope)
```

> Note: `packaging/server.py`'s `__main__` calls `uvicorn.run(app, …)` with the
> *app object*, which cannot hot-reload. Dev mode must use the import-string CLI
> form above (no change to `server.py` required).

Edit any `.py` / prompt / tool → uvicorn reloads in ~1s → the next UI action hits
new code. **No freeze, no publish, no version bump, no UI rebuild.**

Dev mode assumes the source environment is set up (the email package is
importable). If `uvicorn` or the package can't be imported, fail loudly with the
exact remedy (`uv pip install -e hub/agents/python/email`) — **no silent
auto-install, no fallback to user mode.**

## Data flow (both modes, identical)
1. Renderer issues an email action (e.g. "triage my inbox").
2. Request goes to `http://127.0.0.1:8131/v1/email/*` (base URL from the manager).
3. Sidecar (binary or uvicorn) reads the `~/.gaia` Google grant and calls Gmail.
4. Sidecar returns the triage/draft result; renderer renders the existing cards.

## Error handling (fail loudly — no silent fallbacks)
- Binary missing in user mode → surface the npm package's `BinaryNotFoundError`
  with its actionable fetch hint; do **not** silently fall back to dev mode.
- `/health` not ready within timeout → `HealthTimeoutError`; show a UI error with
  the sidecar's last stderr lines.
- Lemonade unreachable → sidecar already returns HTTP 502; surface it verbatim.
- Dev mode requested but Python/source missing → loud startup error naming the
  expected path and how to set up the source checkout. No fallback to user mode.
- Port 8131 in use → fail with the conflicting-process hint (never silently pick
  another port the renderer can't find). **Never touch 4001.**

## Testing
- **Unit (Node):** `EmailSidecarManager` mode selection, spawn-arg shape
  (assert dev mode uses the `--reload` import-string form; user mode uses
  `resolveBinaryPath`), health-poll, tree-kill on shutdown.
- **Integration:** with a running sidecar (both modes), `GET /health` then a real
  `/v1/email/triage` round-trip — proves the *call is valid*, not just invoked.
- **Cold-start check:** confirm the frozen binary bundles `gaia.connectors` so user
  mode can read the grant (verify from an empty-state machine, not a warm box).
- **Prompt/LLM changes** still require `gaia eval agent` against the email category
  vs. baseline before "done" — iteration speed ≠ skipping verification.

## Scope / YAGNI
- **In:** `EmailSidecarManager`, mode flag, both spawn paths, health + shutdown,
  tests, a short dev-mode doc.
- **Out (follow-ups):** Settings-UI toggle (env/config flag is enough now);
  generalizing the manager to other hub agents; multi-mailbox/Outlook specifics
  (handled by the agent, not this integration).

## Relationship to epic #1767
#1767 / PR #1785 rips out the *in-process* email agent. This design is the
*consumption* side — how the UI talks to the **out-of-process sidecar** that
replaces it. They are complementary; this should land on top of (or coordinate
with) the rip-out so the UI has exactly one email backend: the sidecar.

## Resolved decisions
1. **Frozen binary on disk:** lazy-fetched into a `~/.gaia/agents/email/` cache on
   first run (via the npm package's `fetchBinary`/`binaryExists`). No
   installer/build-pipeline change; offline after first fetch; loud failure if the
   fetch fails.
2. **Dev-mode environment:** assume the source env is ready; if the package isn't
   importable, fail loudly with `uv pip install -e hub/agents/python/email`. No
   silent auto-install, no fallback to user mode.
