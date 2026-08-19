# Changelog

All notable changes to `@amd-gaia/gaia` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this package adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — unreleased

First release. `npx @amd-gaia/gaia` is now the single command that gets a user
running GAIA: it fetches and verifies everything GAIA needs and drops them into
the terminal UI. Before this there was no packaged path at all — the flagship agent
had to be run from a repo checkout with a Python environment, and reaching the
terminal UI meant building it from source.

### Added

- **`503` from `/query` at session capacity.** When every retained session
  slot is busy and none is idle enough to evict, starting a new session
  returns `503` with the reason in `detail` — retryable, distinct from a
  bug-shaped `500`. See SPEC §5.2.
- **Per-turn skill-body selection.** A loaded skill stays loaded, but its body
  only renders in the prompt on turns whose query matches its description; the
  rest collapse to a one-line menu the model re-activates with `load_skill`.
  `GAIA_DYNAMIC_SKILLS=0` turns the selection off, `GAIA_DYNAMIC_SKILLS_TAU`
  overrides the match threshold, and an embedder outage disables it for the
  session (every body renders — capability is never lost to a failed match).
- **`gaia run` (the default command)** — resolves the host platform, fetches and
  SHA-256 verifies both binaries, then launches the terminal UI and propagates its
  exit code. Arguments after a bare `--` are forwarded to the TUI verbatim.
- **Dual-binary delivery.** The package installs two published artifacts: the
  frozen agent sidecar (`gaia-agent`), published by this package's own release,
  and the terminal UI (`gaia-tui`), which is the published **`terminal-hub`**
  component. The TUI is consumed, not rebuilt — it is byte-for-byte the binary a
  full GAIA install runs as `gaia tui`, so an npm user and a core user cannot end
  up on terminal UIs that behave differently. A second build under this package's
  own lane would have been the same bytes at a different version under a third
  naming convention, and the two would have drifted.
- **`binaries.lock.json` `schemaVersion` 3.0** — a component-keyed checksum
  manifest where **each component carries its own `componentVersion`, `baseUrl`
  and `platforms`**. Component-first rather than the email agent's flat `binaries`
  map because the two differ in every dimension: hub lane, version, and platform
  coverage (terminal-hub covers arm64 Linux and arm64 Windows; the PyInstaller
  sidecar does not). A single shared base URL cannot address two lanes, so a
  `1.x`- or `2.x`-shaped lock is rejected at load with an error naming the schema.
- **Terminal-hub artifact naming is handled in data.** That lane names its Windows
  builds `gaia-win-x64.exe` / `gaia-win-arm64.exe`, while platform keys come from
  `process.platform` and say `win32`. The lock keeps the `win32-*` key and carries
  the hub's spelling in `filename`, so nothing branches on platform to construct a
  URL. The mapping is asserted on both sides (`TUI_ARTIFACT_NAMES` in
  `src/platform.ts` and in the lock generator) because a wrong name there is not a
  build failure anywhere — it is a 404 on a user's first run.
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
- **`gaia version`** — prints, per component, its version, the URL it is fetched
  from, and its platform matrix.
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
  `gaia` bin shim npm places on `PATH` — the terminal-hub artifact itself is named
  `gaia-<platform>`, which is why the lock separates `filename` from `executable`.
- Because the TUI comes from the `terminal-hub` lane, this package cannot be
  released until that component is published at the version the lock pins. The
  release fails loudly naming the required version; it never falls back to
  building its own TUI. Each terminal-hub artifact is additionally cross-checked
  against the hub's own server-side SHA-256 before its hash enters the lock.
- Requires Node.js 18+ (built-in `fetch`), a running Lemonade Server for
  inference, and the `gaia` Python CLI on `PATH` for the daemon the TUI starts.
- The sidecar has no arm64 Linux or arm64 Windows build. On those platforms the
  run stops with an error naming the platform and the supported set rather than
  launching a UI with no agent behind it.
- `gaia_agent` 0.1.0 has no caller-auth token, so unlike
  `@amd-gaia/agent-email` this package mints and sends none.
- Tracks sidecar contract `apiVersion` **2.12**; a differing major raises
  `VersionMismatchError`.
