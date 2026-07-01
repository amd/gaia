# Changelog

All notable changes to `@amd-gaia/agent-email` are documented here. The package
follows [SemVer](https://semver.org/): the **MAJOR** of the on-the-wire
`SCHEMA_VERSION` is what `checkVersion` enforces at startup, so a contract MAJOR
bump is always at least a package MINOR bump with a migration note.

## Unreleased

### Added

- **Follow-up tracking (#1606).** The agent gains a read-only `check_followups`
  tool that scans the Sent folder of every connected mailbox and flags threads
  whose latest message is still the user's own outbound mail past a
  configurable window (default 3 days) — surfacing message id, recipient,
  subject, and age, most overdue first. Detection only: it never sends a
  nudge (any send stays confirmation-gated). Agent-loop surface (chat /
  Agent UI / `gaia email`); the sidecar REST/MCP surface is unchanged and
  `SCHEMA_VERSION` stays `2.0`.

## 0.2.2

Release-reliability fix. The 0.2.1 tag published the per-platform binaries to the
Agent Hub but its npm publish never completed: the Hub Worker buffered the entire
upload in memory, so the new ~177 MB whole-package zip exceeded Cloudflare's
128 MB per-Worker memory limit and the publish step 502'd before npm. 0.2.2 fixes
that and is the first complete publish of the 0.2.1 feature set. No agent
wire-contract change — `SCHEMA_VERSION` stays `2.0` and the client + REST/MCP
surface are unchanged.

### Fixed

- **Whole-package zip + npm publish now complete.** The Agent Hub Worker
  (`POST /publish`) streams large `application/octet-stream` uploads straight to
  R2 with server-side SHA-256 verification instead of reading the whole body into
  memory, so the ~177 MB whole-package zip publishes without OOMing the Worker.

## 0.2.1

Adds the one-command `playground` launcher and automatic sidecar cleanup, and
makes this README the single canonical agent README (hub + npm). No wire-contract
change (`SCHEMA_VERSION` stays `2.0`).

### Added

- **`npx @amd-gaia/agent-email playground` — one-command launcher.** Fetches the
  binary, starts the sidecar, and opens the browser to `/v1/email/playground`,
  running until Ctrl+C. `--port <n>` to bind elsewhere, `--no-open` to skip the
  browser, `--out <dir>` to choose the binary cache. Makes "try the agent" a single
  command instead of fetch → spawn → find-the-URL.
- **Automatic sidecar cleanup (`autoCleanup`, default on).** `startSidecar` /
  `spawnSidecar` now reap the frozen sidecar's detached process tree when the host
  process exits, crashes (`uncaughtException` / `unhandledRejection`), or is
  interrupted (`SIGINT` / `SIGTERM` / `SIGHUP`) — so a skipped or missed
  `shutdown()` no longer leaves the binary running and holding its port. Pass
  `autoCleanup: false` to manage the lifecycle yourself; `shutdown()` stays the
  graceful, awaited path. A hard `SIGKILL` of the host process is the one case no
  in-process handler can catch.

### Changed

- **This README is now the single canonical agent README** (hub + npm). The
  release workflow publishes it to both, so the hub page and the npm listing no
  longer drift — the architecture diagram and the GAIA/npm install flow show up on
  `hub.amd-gaia.ai/hub/email`.
- **Architecture diagram loads from an in-repo raw image** instead of a
  version-pinned hub URL that the publish pipeline didn't populate, so it renders on
  GitHub and npm before the binaries are uploaded.
- **Agent version is shown on the hub cards** (listing + featured + detail).

## 0.2.0

### Changed (breaking)

- **Contract `SCHEMA_VERSION` 1.0 → 2.0.** The TS client and the frozen binary now
  agree on `"2.0"`. A 0.1.x client against a 2.0 binary (or vice-versa) fails loudly
  at `startSidecar` with `VersionMismatchError` — upgrade both together. No
  `expectedApiVersion` override is needed once client and binary match.

### Added

- **Browser-safe `./client` subpath export.** `import { EmailClient } from
  "@amd-gaia/agent-email/client"` exposes only zero-Node-dependency symbols, so the
  client bundles for a browser or Electron renderer. The `.` entry (binary fetch +
  sidecar spawn) stays Node-only.

## 0.1.0

- Initial release: typed `EmailClient`, build-time `fetch` CLI (download +
  SHA-256 verify), and sidecar lifecycle helpers (`spawnSidecar`, `waitForHealth`,
  `checkVersion`, `shutdown`, `startSidecar`).
