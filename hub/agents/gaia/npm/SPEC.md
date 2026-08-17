# `@amd-gaia/gaia` — technical reference

Version **0.1.0**. Companion to [`README.md`](./README.md), which is the
user-facing doc. This file specifies the wire and file formats the package
depends on and the guarantees it makes.

---

## 1. Scope

`@amd-gaia/gaia` is a **binary delivery and process-lifecycle package**. It ships
no agent logic. It owns:

- resolving the host platform to a lock key,
- downloading and **SHA-256 verifying** two published binaries — one from this
  package's own hub lane, one from the `terminal-hub` component's,
- caching them in stable, versioned locations,
- launching the terminal UI, or the agent sidecar directly.

It does **not** own the GAIA daemon's lifecycle, the sidecar's supervision, or
any agent behaviour. See §6 for where those boundaries sit.

---

## 2. `binaries.lock.json`

Ships inside the published package (`files` includes it). It is the single source
of truth for what is downloaded and what it must hash to.

### 2.1 Schema (`schemaVersion` `3.0`)

```jsonc
{
  "schemaVersion": "3.0",
  "agentVersion": "0.1.0",
  "components": {
    "sidecar": {
      "componentVersion": "0.1.0",
      "baseUrl": "https://hub.amd-gaia.ai/agents/gaia/0.1.0",
      "platforms": { "<platformKey>": { /* entry */ } }
    },
    "tui": {
      "componentVersion": "0.23.0",
      "baseUrl": "https://hub.amd-gaia.ai/agents/terminal-hub/0.23.0",
      "platforms": { "<platformKey>": { /* entry */ } }
    }
  }
}
```

A component lane:

| Field              | Type     | Meaning                                                    |
| ------------------ | -------- | ---------------------------------------------------------- |
| `componentVersion` | `string` | That component's own released version                       |
| `baseUrl`          | `string` | Where **that component's** artifacts are served from        |
| `platforms`        | `object` | Platform key → entry                                        |

An entry:

| Field        | Type     | Meaning                                                     |
| ------------ | -------- | ----------------------------------------------------------- |
| `filename`   | `string` | Artifact name as published under **its component's** `baseUrl` |
| `executable` | `string` | Basename it is written as on disk (with the platform extension) |
| `sha256`     | `string` | Lowercase hex SHA-256 the download must match                |
| `size`       | `number` | Informational. **Not** enforced                              |

**Why per-component, and why `3.0`.** `2.0` had one top-level `baseUrl` shared by
both components. That stopped being true once the TUI became the published
`terminal-hub` component (§2.2): the two live in different hub lanes at different
versions. A shared base URL cannot express that, so `2.x` is rejected at load with
an error naming the schema — never read as `3.x` with a missing field.

The email agent's lock is `schemaVersion` `1.0`, a flat `binaries` map, because it
delivers one binary from one lane. A `1.x`-shaped lock is likewise rejected.

### 2.2 Where each component comes from

| Component | Hub lane                              | Published by            |
| --------- | ------------------------------------- | ----------------------- |
| `sidecar` | `agents/gaia/<agentVersion>/`         | this package's release  |
| `tui`     | `agents/terminal-hub/<componentVersion>/` | the core GAIA release |

The TUI is **consumed, not built here**. It is the same `tui/cmd/gaia` binary the
core release publishes as the `terminal-hub` component and a core install runs as
`gaia tui` — so behaviour is identical by construction rather than by convention.
Building a second copy under this package's lane would put the same bytes at a
different version under a third naming convention, and the two would drift.

The consequence is a real release dependency: this package cannot ship until
`terminal-hub` is published at the version its lock pins. The release fails loudly
naming that version; it never falls back to building its own TUI.

The `tui` lane's origin is pinned in the lock rather than derived from the release
pipeline's hub-origin variable, so the release verifies the exact URL the shipped
lock will send users to. A release pointed at a non-default origin therefore moves
the sidecar lane and **not** the TUI lane; re-point `components.tui.baseUrl` too if
you need both.

### 2.3 Platform keys

`` `${process.platform}-${process.arch}` `` — e.g. `win32-x64`, `darwin-arm64`,
`linux-x64`. This matches `gaia.daemon.sidecars.platform.current_platform_key()`,
which normalises Python's `sys.platform` / `platform.machine()` into the same
namespace so the daemon and this package agree on a cache key.

**The `win32` ↔ `win` mapping.** The `terminal-hub` lane names its Windows
artifacts `gaia-win-x64.exe` / `gaia-win-arm64.exe` (Go's `GOOS` vocabulary), while
our keys come from `process.platform`, which says `win32`. The lock keeps the
`win32-*` key and carries the hub's spelling in `filename`:

| Platform key   | `tui` `filename`         |
| -------------- | ------------------------ |
| `win32-x64`    | `gaia-win-x64.exe`       |
| `win32-arm64`  | `gaia-win-arm64.exe`     |
| `darwin-x64`   | `gaia-darwin-x64`        |
| `darwin-arm64` | `gaia-darwin-arm64`      |
| `linux-x64`    | `gaia-linux-x64`         |
| `linux-arm64`  | `gaia-linux-arm64`       |

The mapping therefore lives in **data**, not in a code path — nothing branches on
the platform to build a name. `TUI_ARTIFACT_NAMES` (in `src/platform.ts` and in
`packaging/gen_binaries_lock.py`) is the authority both the shipped lock and the
release pipeline are checked against, because a wrong name here is not a build
failure anywhere: it is a 404 on a user's first run.

### 2.4 Platform coverage

| Platform key   | `sidecar` | `tui` |
| -------------- | :-------: | :---: |
| `win32-x64`    |    yes    |  yes  |
| `darwin-arm64` |    yes    |  yes  |
| `darwin-x64`   |    yes    |  yes  |
| `linux-x64`    |    yes    |  yes  |
| `linux-arm64`  |  **no**   |  yes  |
| `win32-arm64`  |  **no**   |  yes  |

`terminal-hub` publishes all six. The sidecar is a PyInstaller freeze, produced on
the platform it targets, and there is no arm64 Linux or arm64 Windows freeze.
Resolving `sidecar` on those two keys raises `PlatformError` naming the platform
and the supported set — it is not silently skipped, and the TUI is not launched
without an agent behind it.

### 2.5 Placeholder hashes

Between releases every `sha256` is `PENDING-replace-with-real-sha256`. The release
pipeline regenerates the file with real hashes: for the sidecar, computed from the
artifacts it just published; for the TUI, computed from the `terminal-hub`
artifacts it downloaded and cross-checked against that lane's own recorded hashes
(`agents/terminal-hub/manifest.json`, which the hub computes server-side at publish
time). Both are then re-fetched from the public origin and re-hashed before the
release is allowed to ship.

`isPlaceholderSha()` treats a value as a placeholder when it is all zeros or
contains `PENDING` (case-insensitive). A placeholder **blocks the fetch before any
network call** with a `PlatformError`. There is no flag, env var, or option that
relaxes this.

---

## 3. Integrity

The SHA-256 check is the package's security boundary.

- Downloaded bytes are hashed in memory and compared against the lock **before
  anything is written to the cache path**.
- On mismatch: `IntegrityError`, message naming expected vs actual, nothing left
  on disk.
- Writes go to `<path>.download.<pid>` and are `rename`d into place, so a crash
  mid-write never leaves a partial file that a later run treats as verified.
- A cache hit requires re-hashing the on-disk file and matching the lock.
  A cached file whose bytes drifted is re-downloaded, not reused.
- POSIX installs `chmod 0o755` after the rename.

There is no unverified path, no "warn and continue", and no way to disable the
check.

---

## 4. Filesystem layout

| Component | Default directory              | Executable          |
| --------- | ------------------------------ | ------------------- |
| `sidecar` | `~/.gaia/agents/gaia/`         | `gaia-agent[.exe]`  |
| `tui`     | `~/.gaia/npm-cache/gaia-<agentVersion>/` | `gaia-tui[.exe]` |

The sidecar directory is a **cross-repo contract** with
`gaia.daemon.sidecars.fetch.default_cache_dir("gaia")`. The daemon spawns and
supervises the sidecar and does its own SHA-256 check on the binary it finds
there; installing an already-verified binary at that path turns the daemon's fetch
into a cache hit instead of a second multi-hundred-megabyte download.

The TUI directory is keyed by `agentVersion` — this package's version, not the
component's — so a bump of either never reuses the previous release's executable.

The TUI executable is renamed to `gaia-tui` on install, **never** `gaia`: the
`terminal-hub` artifact is `gaia-<platform>` and npm installs a `gaia` bin shim on
`PATH`, so keeping the hub's name would shadow the shim. `filename` (what is
downloaded) and `executable` (what is written) are separate fields for exactly this
reason, and `test/lock.test.ts` asserts it for every platform.

Both defaults are overridable (`--sidecar-dir`, `--cache-dir`).

---

## 5. Sidecar HTTP surface

Served by `gaia_agent.server`. Bound to `127.0.0.1` only.

| Property           | Value                                   |
| ------------------ | --------------------------------------- |
| Default port       | `8141` (`DEFAULT_PORT` in `server.py`)  |
| Reserved port      | `4001` — refused with a `RangeError`    |
| Contract version   | `API_VERSION = "2.12"`                  |
| Agent id / prefix  | `gaia` → `/v1/gaia/...`                 |

### 5.1 Endpoints

| Method | Path                             | Purpose                                        |
| ------ | -------------------------------- | ---------------------------------------------- |
| `GET`  | `/health`                        | Liveness. `{ "status": "ok", "service": "gaia-agent-gaia" }` |
| `GET`  | `/version`                       | Contract probe. `{ "apiVersion", "agentVersion" }` |
| `GET`  | `/v1/gaia/version`               | The TUI's negotiation probe                    |
| `GET`  | `/v1/gaia/init`                  | Readiness detail (Lemonade, model, connectors) |
| `POST` | `/v1/gaia/query`                 | The streaming surface (`text/event-stream`)    |
| `POST` | `/v1/gaia/query/{run_id}/cancel` | Cancel a run by its host-minted `run_id`       |
| `POST` | `/v1/gaia/query/{run_id}/respond`| Answer a mid-run question                      |

`/health` is liveness only. It says nothing about whether Lemonade is up or a
model is loaded — `/v1/gaia/init` answers that.

### 5.2 `session_id` and agent retention

`POST /v1/gaia/query` accepts an optional `session_id` in the request body.
**Pass it on every call in a conversation, and reuse the same value for the
whole conversation.** Contract ≥ 2.12 resolves `session_id` to a *retained*
agent instead of a throwaway built fresh per call — indexed documents and
`load_skill` state only survive between turns when the same `session_id`
threads them together.

A retained skill stays *loaded* but its body is not necessarily in the prompt
every turn: the agent selects per turn which loaded bodies match the query and
collapses the rest to a one-line menu entry (re-activated by calling
`load_skill` again). `GAIA_DYNAMIC_SKILLS=0` disables the selection;
`GAIA_DYNAMIC_SKILLS_TAU=<float>` overrides its threshold; an embedder outage
disables it for the session and every body renders. Omitting it is a valid, explicit one-shot: nothing
persists past that single turn, and the agent is not told otherwise.

A second `/query` for a `session_id` that already has a turn in flight gets
`409 Conflict` — cancel the running turn or wait for it, then retry. A
`session_id` can also be evicted from the retention table under an idle
timeout or an LRU cap on concurrent sessions; a `/query` that lands on an
evicted id gets a fresh agent (the conversation is not blocked) but the
response stream's first event is a `{"type":"status","status":"warning",...}`
telling the caller that per-turn state — most visibly any loaded skill — did
not survive and should be reloaded.

### 5.3 Version gate

`checkVersion()` reads `/version` and compares the **major** of `apiVersion`
against this package's `API_VERSION`. A differing major is a breaking contract
change and raises `VersionMismatchError`. A higher minor with the same major is a
backward-compatible addition and is accepted.

### 5.4 No caller-auth token

The email sidecar authenticates callers with a per-session bearer minted into
`GAIA_EMAIL_SIDECAR_TOKEN`. `gaia_agent` has **no equivalent** at 0.1.0, so
this package mints and sends nothing. When the sidecar grows one, it lands here as
a spawn-time env var and a request header — a change to this section, not a new
subsystem.

---

## 6. Process ownership

Two distinct paths, deliberately:

### 6.1 `gaia run` — the normal path

`run` fetches, verifies, installs, and **execs the TUI**. It does not spawn a
sidecar.

That is not an omission. The TUI reaches agents through the GAIA daemon's relay
(`/v1/<agent>/*`) and holds only the daemon client token, never a sidecar bearer —
`tui/internal/daemon` states this as an invariant. The TUI start-or-attaches the
daemon under an advisory lock, and the daemon spawns and supervises the sidecar
from `~/.gaia/agents/gaia/`. A sidecar spawned here would be a second process the
TUI never talks to, competing for port `8141`.

So `run`'s contribution to the sidecar is putting a **verified** binary where the
daemon looks. Daemon and sidecar lifecycle belong to the daemon.

### 6.2 `gaia serve` — the direct path

`serve` fetches the sidecar and spawns it itself, for integrators who want the
REST surface without a daemon or a UI. This path owns the process fully: spawn →
`waitForHealth` → `checkVersion` → run until interrupted → tree-kill.

### 6.3 Tree-kill

The sidecar is a PyInstaller one-file build: it unpacks and spawns a child uvicorn
process that `child.kill()` on the parent does **not** reap, leaving port `8141`
bound. Every teardown kills the whole tree:

- **POSIX** — spawned `detached` so the child leads its own process group;
  `process.kill(-pid, SIGTERM)`, escalating to `SIGKILL` after the timeout.
- **Windows** — `taskkill /PID <pid> /T /F`.

`autoCleanup` (default `true`) also reaps on `exit`, `SIGINT`, `SIGTERM`,
`SIGHUP`, `uncaughtException`, and `unhandledRejection`. A `SIGKILL` of the parent
is the one case no in-process handler can catch.

`startSidecar()` shuts the sidecar down before rethrowing on any failure, so a
failed start never leaks a process.

---

## 7. Exit codes

| Code    | Meaning                                                                 |
| ------- | ----------------------------------------------------------------------- |
| `0`     | Success                                                                 |
| `1`     | A typed failure: `IntegrityError`, `PlatformError`, `HealthTimeoutError`, `VersionMismatchError`, `BinaryNotFoundError`, or an unexpected error |
| `2`     | Usage error: unknown command, unknown `--component`, invalid `--port`    |
| *other* | From `run`: the TUI's own exit code, propagated verbatim                 |

A TUI killed by a signal propagates as `128 + signum` (the shell convention), so a
Ctrl+C is distinguishable from a clean `0`.

---

## 8. Errors

All extend `GaiaError`, so `instanceof GaiaError` catches any of ours.

| Class                  | Raised when                                                    |
| ---------------------- | -------------------------------------------------------------- |
| `IntegrityError`       | A download's SHA-256 does not match the lock                   |
| `PlatformError`        | Unsupported platform, missing/incomplete entry, placeholder hash, malformed lock |
| `HealthTimeoutError`   | The sidecar did not report healthy within the timeout          |
| `VersionMismatchError` | `apiVersion` major differs from this package's                 |
| `BinaryNotFoundError`  | A binary is absent from disk when spawning                     |
| `HttpError`            | A non-2xx from a sidecar probe                                 |

Per the repo's no-silent-fallbacks rule, every message names what failed, what to
do, and where to look next.

---

## 9. Timeouts

| Operation             | Default   | Why                                              |
| --------------------- | --------- | ------------------------------------------------ |
| Download (per binary) | `300000`ms | The frozen sidecar is a large artifact           |
| Health wait           | `60000`ms  | A cold one-file build unpacks before it binds    |
| Health poll interval  | `250`ms    |                                                  |
| `/health` probe       | `1000`ms   |                                                  |
| `/version` probe      | `5000`ms   |                                                  |
| Shutdown grace        | `5000`ms   | Then `SIGKILL` / forced                          |

---

## 10. Public API

Exported from the package root — see `src/index.ts`.

**Fetch:** `fetchAll`, `fetchBinary`, `verifySha256`, `fileSha256`,
`binaryExists`, `defaultCacheDir`, `daemonSidecarCacheDir`.

**Lifecycle:** `spawnSidecar`, `startSidecar`, `waitForHealth`, `checkVersion`,
`health`, `version`, `shutdown`, `runTui`, `resolveSidecarPath`, `resolveTuiPath`,
`sidecarExecutableName`, `tuiExecutableName`.

**Platform:** `currentPlatformKey`, `loadLock`, `resolveEntry`, `componentLock`,
`componentBaseUrl`, `platformsFor`, `defaultLockPath`, `isPlaceholderSha`,
`COMPONENTS`, `SCHEMA_MAJOR`, `TUI_ARTIFACT_NAMES`,
`SUPPORTED_SIDECAR_PLATFORMS`, `SUPPORTED_TUI_PLATFORMS`, `SUPPORTED_PLATFORMS`.

**Constants:** `AGENT_ID`, `API_VERSION`, `DEFAULT_HOST`, `DEFAULT_PORT`,
`RESERVED_PORT`.

---

## 11. Logging

`DEBUG=gaia` (or `DEBUG=*`) enables debug output. **Everything goes to stderr** —
stdout belongs to the TUI once it is exec'd, and to machine-readable JSON for
`fetch` / `version`.
