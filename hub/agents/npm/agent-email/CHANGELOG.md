# Changelog

All notable changes to `@amd-gaia/agent-email` are documented here. The package
follows [SemVer](https://semver.org/): the **MAJOR** of the on-the-wire
`SCHEMA_VERSION` is what `checkVersion` enforces at startup, so a contract MAJOR
bump is always at least a package MINOR bump with a migration note.

## 0.2.1

Documentation/packaging release — no client API or wire-contract change
(`SCHEMA_VERSION` stays `2.0`). Republishes so the live hub catalog picks up the
current README.

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
