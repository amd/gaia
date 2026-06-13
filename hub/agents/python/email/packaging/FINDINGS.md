# De-risk spike — frozen email-agent binary (milestone #49)

**Verdict: ✅ PASS.** A single self-contained PyInstaller executable boots the GAIA
email agent's REST server and answers requests with **no Python interpreter**
present, on Windows x64 (this machine's platform). The riskiest assumption of the
email-agent-packaging milestone (Phase 2 of `docs/plans/email-agent-packaging.mdx`)
holds.

The binary serves the full email REST surface — `/v1/email/triage`, `/draft`,
`/send` (the frozen `#1262` contract) plus `/health` and `/version` — and a
fixture-derived `POST /v1/email/triage` returns a **contract-valid** response,
all against a stubbed LLM backend (no live Gmail/Outlook, no live model).

---

## Environment

| | |
|---|---|
| Platform | Windows 10/11, x64 (`win32-x64`) |
| Python (build venv) | 3.12.13 (uv picked 3.12 for the venv; repo also has 3.13 on PATH) |
| Freezer | PyInstaller 6.20.0 (+ pyinstaller-hooks-contrib 2026.6) |
| Core install | `uv pip install --system-certs -e ".[api]"` (editable) |
| Email install | `uv pip install --system-certs -e hub/agents/python/email` (editable) |

> **Corporate-TLS gotcha:** plain `uv pip install` failed with
> `invalid peer certificate: UnknownIssuer` behind the corporate proxy. Adding
> **`--system-certs`** fixed it. CI on hosted runners won't hit this; document it
> for local/on-prem builds.

---

## Build

```bash
# from an activated venv with .[api] + the email package + pyinstaller installed
python hub/agents/python/email/packaging/freeze.py            # one-dir (default, recommended)
python hub/agents/python/email/packaging/freeze.py --onefile  # one-file (single .exe)
```

| Mode | Output | Size | Cold-start to `/health` |
|------|--------|------|--------------------------|
| **one-dir** (recommended) | `dist/email-agent/email-agent.exe` (+ `_internal/`) | **86.2 MB** total | **~3.4 s** |
| one-file | `dist/email-agent.exe` (single file) | **41.7 MB** | **~5.9 s** |

Build time ≈ 150–170 s each. One-file is ~half the on-disk size (LZMA-compressed)
but pays a per-launch self-extraction cost to `%TEMP%\_MEIxxxxxx` (the ~2.5 s
delta above) and leaves a temp dir; one-dir starts faster and is friendlier to
code-signing (sign the `.exe`, ship the folder).

### Launch

```bash
# spike default: stubbed LLM so it serves with zero backend deps
dist/email-agent/email-agent.exe --host 127.0.0.1 --port 8131
# production shape (real local triage via Lemonade):
dist/email-agent/email-agent.exe --host 127.0.0.1 --port 8131 --no-stub-llm
```

Flags: `--host` (default `127.0.0.1`), `--port` (default **8131** — deliberately
**not 4001**), `--no-stub-llm` (use the real local Lemonade model instead of the
deterministic stub), `--print-openapi` (dump OpenAPI and exit).

---

## Proof (smoke test)

```bash
python hub/agents/python/email/packaging/smoke_test.py dist/email-agent/email-agent.exe
```

The harness launches the **binary only** (a subprocess; the harness itself is
Python, the *server* is not), polls `/health`, then checks OpenAPI, `/version`,
and a triage round-trip. Final run (one-dir, isolated, clean shutdown):

```
[smoke] /health ready after 2.6s -> {'status': 'ok', 'service': 'gaia-agent-email'}
[smoke] startup time to /health: 3.4s
[smoke] openapi paths: ['/health', '/v1/email/draft', '/v1/email/send', '/v1/email/triage', '/version']
[smoke] /version -> {'apiVersion': '1.0', 'agentVersion': '0.1.0'}
[smoke] fixture-derived message: subject='Prod incident follow-up' from='Sarah Chen <...>'
[smoke] triage HTTP 200 -> {"schema_version":"1.0","request_kind":"single","result":{"category":"actionable", ...}}
[smoke] contract-valid triage response: request_kind=single category=actionable
[smoke] VERDICT: PASS
```

The triage body is derived from `tests/fixtures/email/synthetic_inbox.mbox`
(first message). The response is validated against the real contract via
`gaia_agent_email.contract.parse_response` — not a string match.

### No-Python rigor

Confirmed the binary carries its own interpreter and needs no ambient Python:

- `dist/email-agent/_internal/python312.dll` + `python3.dll` are bundled.
- Launched with a **scrubbed environment** (`PYTHONHOME=` `PYTHONPATH=`
  `PATH=C:\Windows\System32`) it still served `/health` and `/version` 200 OK.

---

## What the spike stubs (and why it's honest)

`POST /v1/email/triage` normally calls the local Lemonade LLM
(`classify_email_llm` + `summarize_email_llm`). To keep the spike free of a live
model and live mail, `server.py --stub-llm` (default ON) swaps the triage
service's chat client for a deterministic stub that returns a contract-valid
classification + summary. This is the exact seam the plan calls for ("use
dependency overrides / a stub backend so the spike does not need live Gmail or a
live LLM").

- The **REST surface, routing, request/response validation, and the entire
  frozen contract** are exercised for real — only the model call is stubbed.
- `/v1/email/send` (the confirmation-gate path) is **mounted and reachable** but
  not exercised end-to-end: a real send needs a connected mailbox. Its 403
  no-token gate is pure-python and would work frozen; the live keyring/OAuth
  backend resolution is untested at runtime (see gotchas).

---

## Gotchas (what needed handling)

1. **Editable installs are invisible to PyInstaller.** With `-e` installs,
   `gaia_agent_email` and `gaia` are not discoverable by PyInstaller's static
   analyzer — the first build failed at runtime with
   `ModuleNotFoundError: No module named 'gaia_agent_email'`. **Fix:** add the
   source roots to `--paths` (`hub/agents/python/email` and `src`). CI that does
   a non-editable wheel install won't need this, but the spike (and any local
   editable build) does. `freeze.py` handles it automatically.

2. **uvicorn string-imports its impls.** Loops/protocols/lifespan are imported by
   name at runtime, invisible to static analysis. **Fix:**
   `--collect-submodules uvicorn`.

3. **keyring resolves OS backends via entry points.** `gaia.connectors.store`
   does `import keyring` at module load (reached when the email router mounts),
   and keyring finds its Windows backend through `importlib.metadata` entry
   points. **Fix:** `--collect-submodules keyring` **and** `--copy-metadata
   keyring`. The binary boots and mounts the router, proving keyring imports
   under freeze. The actual Windows credential-store *read* (DPAPI via the
   `keyring.backends.Windows` backend, which needs `pywin32`) is **not exercised**
   here — validate it when the `/send` path is wired with a connected mailbox.

4. **Lazy tool imports on the triage path.** The router imports its tool modules
   (`llm_triage`, `summarize_tools`, …) inside functions. **Fix:**
   `--collect-submodules gaia_agent_email` pulls the whole package so the triage
   path is present.

5. **The ML stack bloats the freeze.** The triage path lazily reaches
   `gaia.chat.sdk` → torch/transformers/faiss (~2 GB). With `--stub-llm` none of
   it runs, so `freeze.py` **excludes** the ML stack (`torch`, `transformers`,
   `sentence_transformers`, `faiss`, …), cutting the binary to <90 MB. A
   non-stub binary that runs real triage talks to **Lemonade Server over HTTP**
   (no in-process torch), so the excludes likely hold for production too —
   confirm when building the `--no-stub-llm` production binary.

6. **One-file orphans its child on kill.** PyInstaller's one-file bootloader
   spawns a child; `Popen.terminate()` on the parent leaves the uvicorn child
   (and the listening socket) alive. **Fix in the harness:** kill the process
   **tree** (`taskkill /F /T` on Windows). **Host-app implication:** the sidecar
   supervisor MUST kill the tree on shutdown, not just the spawned PID.

7. **GAIA logger writes to stdout.** `gaia.logger` attaches a colored stdout
   handler on import, so `--print-openapi`'s JSON is preceded by a log line. Not
   a problem for the HTTP path (the smoke test uses `/openapi.json`), but a
   consumer scripting `--print-openapi` should read the last line / use the
   endpoint instead.

---

## Recommendation

- **One-dir over one-file** as the default distributed artifact: faster startup,
  no per-launch temp extraction, and cleaner to code-sign (sign the `.exe`, ship
  the `_internal/` folder alongside). One-file is the better *download* (half the
  size) — fine if a host prefers a single file and can absorb the ~2.5 s extra
  cold start. Both PASS.
- **PyInstaller over Nuitka:** PyInstaller resolved every dependency for this
  app with only the four `--collect-submodules` / `--copy-metadata` / `--paths`
  knobs above — no Nuitka fallback was needed. Keep Nuitka in the back pocket
  only if a future dep defeats PyInstaller's hooks.
- **No blocker foreseen for the other 3 platforms** (`darwin-arm64`,
  `darwin-x64`, `linux-x64`). The app is pure-python + fastapi/uvicorn/pydantic/
  keyring — all cross-platform, all with PyInstaller hooks. Per-platform watch
  items, none expected to block:
  - **keyring backend swaps per OS** (macOS Keychain, Linux SecretService/
    `dbus`/`SecretStorage`). `--collect-submodules keyring` + `--copy-metadata`
    should carry them; the live read still wants a per-OS check once `/send` is
    wired.
  - **macOS** needs sign + **notarize** (Phase 4) or Gatekeeper blocks the
    bundled binary inside a host app.
  - **Linux** glibc floor: build on the **oldest** target glibc (manylinux-style)
    so the binary runs on older distros.
  - Each platform must build **natively** (PyInstaller doesn't cross-compile) —
    the milestone's CI matrix already implies this.

## Reproduce

```bash
uv venv
uv pip install --system-certs -e ".[api]"
uv pip install --system-certs -e hub/agents/python/email
uv pip install --system-certs pyinstaller
python hub/agents/python/email/packaging/freeze.py
python hub/agents/python/email/packaging/smoke_test.py \
    hub/agents/python/email/packaging/dist/email-agent/email-agent.exe
```

Build artifacts (`build/`, `dist/`, `*.spec`) are git-ignored — only `freeze.py`,
`server.py`, `smoke_test.py`, and this file are committed. The 86 MB one-dir
binary is **not** committed; rebuild with the command above.
