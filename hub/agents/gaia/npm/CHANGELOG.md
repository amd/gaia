# Changelog

All notable changes to `@amd-gaia/gaia` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — unreleased

First release. `npx @amd-gaia/gaia` is now the single command that gets a user
running GAIA: it fetches and verifies everything GAIA needs and drops them into
the terminal UI. Previously there was no packaged path at all — the terminal UI
had never been published, existing only as a 14-day CI artifact, and the flagship
agent had to be run from a repo checkout with a Python environment.

### Added

- **`gaia run` (the default command)** — resolves the host platform, fetches and
  SHA-256 verifies both binaries, then launches the terminal UI and propagates its
  exit code. Arguments after a bare `--` are forwarded to the TUI verbatim.
- **Dual-binary delivery.** The package installs two published artifacts: the
  frozen agent sidecar (`gaia-agent`) and the Go terminal UI (`gaia-tui`) — the
  first release of the TUI to ship anywhere but a CI artifact.
- **`binaries.lock.json` `schemaVersion` 2.0** — a component-keyed checksum
  manifest (`components.sidecar` / `components.tui`). Component-first rather than
  the email agent's flat `binaries` map because the two components have different
  platform coverage: the Go TUI cross-compiles to arm64 Linux and arm64 Windows,
  the PyInstaller sidecar does not.
- **Mandatory SHA-256 verification.** Every download is hashed and compared
  against the lock before it is written. A mismatch deletes the download and
  raises `IntegrityError` naming expected vs actual. A placeholder hash blocks the
  fetch before any network call. There is no flag that relaxes either.
- **`gaia fetch`** — download and verify without launching; prints JSON. Supports
  `--component` and `--platform` for cross-platform staging in CI.
- **`gaia serve`** — run the agent sidecar alone on `127.0.0.1:8141` for
  integrators who want the REST surface without a daemon or a UI. Health-polls
  `GET /health`, checks the contract version, and tree-kills on exit. Port `4001`
  is refused.
- **`gaia version`** — prints the lock manifest and the per-component platform
  matrix for the installed version.
- **Programmatic exports** — `fetchAll`, `startSidecar`, `shutdown`, `runTui`, the
  platform helpers, and the typed error classes, for embedding GAIA in another
  app.

### Notes

- The sidecar is installed into `~/.gaia/agents/gaia/`, the GAIA daemon's own
  cache directory. The daemon spawns and supervises the sidecar; putting an
  already-verified binary where it looks turns its fetch into a cache hit instead
  of a second large download. `run` therefore does not spawn a sidecar itself —
  the terminal UI reaches agents through the daemon relay and never holds a
  sidecar token, so a second process would only contend for the port. `serve` is
  the direct path for callers who do want to own it.
- The TUI is installed as `gaia-tui`, never as `gaia`, so it cannot shadow the
  `gaia` bin shim npm places on `PATH`.
- Requires Node.js 18+ (built-in `fetch`), a running Lemonade Server for
  inference, and the `gaia` Python CLI on `PATH` for the daemon the TUI starts.
- The sidecar has no arm64 Linux or arm64 Windows build. On those platforms the
  run stops with an error naming the platform and the supported set rather than
  launching a UI with no agent behind it.
- `gaia_agent_gaia` 0.1.0 has no caller-auth token, so unlike
  `@amd-gaia/agent-email` this package mints and sends none.
- Tracks sidecar contract `apiVersion` **2.12**; a differing major raises
  `VersionMismatchError`.
