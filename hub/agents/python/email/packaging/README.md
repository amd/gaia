# Email agent — frozen binary packaging

Builds the GAIA email agent into a single self-contained executable that boots its
REST server with **no Python interpreter present** on the target machine. This is
the Python side of the email-agent-packaging milestone
(`docs/plans/email-agent-packaging.mdx`); the npm side (`@amd-gaia/agent-email`)
fetches and spawns this binary as a sidecar.

The binary serves the full email REST surface — `/v1/email/triage`, `/draft`,
`/send` (the frozen `#1262` contract) plus `/health` and `/version`.

## Files

| File | Role |
|------|------|
| `server.py` | Minimal FastAPI app mounting only the email router + `/health` + `/version`. The freeze entrypoint (not the whole `gaia api` app, which drags in every agent + the ML stack). |
| `freeze.py` | PyInstaller build. Bakes in the gotcha fixes below. |
| `smoke_test.py` | Launches the built binary as a subprocess and checks `/health`, OpenAPI, `/version`, and a triage round-trip. Used by `release_agent_email.yml`. |
| `gen_binaries_lock.py` | Regenerates `hub/agents/npm/agent-email/binaries.lock.json` from the publish summary (hub URLs + SHA-256). |
| `publish_to_r2.py` | **The release upload path.** POSTs each binary + `gaia-agent.yaml` to the Agent Hub Worker's `POST /publish` (`release_agent_email.yml`), which checksums server-side, stores immutably under `agents/email/<ver>/`, and rebuilds `index.json`. |
| `upload_to_r2.sh` | **Legacy / discouraged.** Hand-upload via rclone straight to the bucket — this **bypasses the Worker**, so `index.json` is NOT rebuilt. Use `publish_to_r2.py` instead. See [`HUB-UPLOAD.md`](HUB-UPLOAD.md). |
| `HUB-UPLOAD.md` | Manual rclone upload guide (the legacy bypass path). |

Build artifacts (`build/`, `dist/`, `*.spec`) are git-ignored; binaries are never committed.

## Build

```bash
# from a venv with .[api] + the email package + pyinstaller installed (see Setup)
python hub/agents/python/email/packaging/freeze.py            # one-dir (default, recommended)
python hub/agents/python/email/packaging/freeze.py --onefile  # one-file (single .exe)
```

| Mode | Output | Size | Cold-start to `/health` |
|------|--------|------|--------------------------|
| **one-dir** (recommended) | `dist/email-agent/email-agent.exe` (+ `_internal/`) | ~86 MB | ~3.4 s |
| one-file | `dist/email-agent.exe` | ~42 MB | ~5.9 s |

Build time ≈ 150–170 s. **Prefer one-dir** as the distributed artifact: faster
start, no per-launch self-extraction to `%TEMP%`, and cleaner to code-sign (sign
the `.exe`, ship the folder). One-file is the smaller *download* but pays a
self-extraction cost on each launch. **PyInstaller, not Nuitka** — the four
`--paths` / `--collect-submodules` / `--copy-metadata` knobs below resolve every
dependency; keep Nuitka only as a fallback if a future dep defeats the hooks.

## Launch

```bash
dist/email-agent/email-agent.exe --host 127.0.0.1 --port 8131
```

Flags: `--host` (default `127.0.0.1`), `--port` (default **8131** — deliberately
not 4001), `--print-openapi` (dump OpenAPI and exit — note `gaia.logger` prepends
a stdout log line; read the last line or use `/openapi.json`).

`/health`, `/version`, and `/openapi.json` are dependency-free, so the binary
boots and serves the contract surface with no model. `POST /v1/email/triage`
uses the **real local Lemonade model** and requires a reachable Lemonade Server;
if there is none it returns **HTTP 502 (`local LLM triage failed`)**.

### Caller authentication (#1706)

The sidecar can send mail as the user, so it authenticates its caller. Set
`GAIA_EMAIL_SIDECAR_TOKEN` in the environment (the `@amd-gaia/agent-email`
lifecycle and the GAIA UI sidecar manager do this automatically on spawn) and
every `/v1/email/*` request must send `Authorization: Bearer <token>` or it is
rejected with **401**. A non-loopback `Host` → **400** (DNS-rebinding) and a
non-loopback browser `Origin` → **403** (drive-by page); `/health`, `/version`,
`/v1/email/spec`, and `/v1/email/playground` are exempt from the token. Launching
by hand without the env var disables the token check (local dev only, logged
loudly) — Host/Origin protection still applies.

## Smoke test

```bash
python hub/agents/python/email/packaging/smoke_test.py dist/email-agent/email-agent.exe
```

Launches the binary only (no `python -m`), polls `/health`, then checks OpenAPI,
`/version`, and a triage round-trip whose response is validated against the real
contract via `gaia_agent_email.contract.parse_response` (not a string match). The
triage body is derived from `tests/fixtures/email/synthetic_inbox.mbox`.

## Build gotchas (baked into `freeze.py`)

1. **Editable installs are invisible to PyInstaller.** `-e` installs aren't found
   by the static analyzer (`ModuleNotFoundError: gaia_agent_email` at runtime).
   Fix: `--paths` to the source roots (`hub/agents/python/email`, `src`). A
   non-editable wheel install in CI won't need this; local editable builds do.
2. **uvicorn string-imports its impls** (loops/protocols/lifespan). Fix:
   `--collect-submodules uvicorn`.
3. **keyring resolves OS backends via entry points.** `gaia.connectors.store`
   imports `keyring` at module load. Fix: `--collect-submodules keyring` **and**
   `--copy-metadata keyring`. The binary boots and mounts the router (keyring
   imports under freeze); the actual credential-store *read* (Windows DPAPI needs
   `pywin32`) is exercised only once `/send` is wired with a connected mailbox.
4. **Lazy tool imports on the triage path.** The router imports tool modules
   inside functions. Fix: `--collect-submodules gaia_agent_email`.
5. **The ML stack bloats the freeze.** The triage path lazily reaches
   `gaia.chat.sdk` → torch/transformers/faiss (~2 GB). Real triage talks to the
   local Lemonade Server over HTTP (no in-process torch), so `freeze.py`
   **excludes** them, cutting the binary to <90 MB.
6. **One-file orphans its child on kill.** The one-file bootloader spawns a child;
   `terminate()` on the parent leaves the uvicorn child (and its socket) alive.
   The host sidecar supervisor MUST kill the process **tree** (`@amd-gaia/agent-email`
   does this).

## Per-platform notes

Each platform must build **natively** (PyInstaller doesn't cross-compile) — the
release workflow's matrix does this. No blocker expected for `darwin-arm64`,
`darwin-x64`, `linux-x64` (pure-python + fastapi/uvicorn/pydantic/keyring, all
with hooks). Watch items:

- **keyring backend swaps per OS** (macOS Keychain, Linux SecretService). The
  collect/copy-metadata flags should carry them; the live read wants a per-OS
  check once `/send` is wired.
- **macOS** needs sign + notarize (else Gatekeeper blocks the bundled binary).
- **Linux** build on the oldest target glibc (manylinux-style) for broad compat.

## Setup (reproduce)

```bash
uv venv
uv pip install -e ".[api]"
uv pip install -e hub/agents/python/email
uv pip install pyinstaller
```

> **Corporate-TLS:** if `uv pip install` fails with `invalid peer certificate:
> UnknownIssuer` behind a proxy, add `--system-certs`. Hosted CI runners don't
> hit this.
