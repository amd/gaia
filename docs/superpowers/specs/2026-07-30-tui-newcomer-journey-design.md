# Terminal hub as the primary newcomer entry point

Date: 2026-07-30
Status: approved design, pending implementation plan

## Problem

A newcomer with nothing installed cannot get the terminal hub from the website. Today:

- The landing CTA "Get the terminal hub" points at `docs/reference/cli`, a command
  reference for a binary the visitor does not have.
- `/hub/terminal-hub` does not exist. `release_components.yml` publishes the component
  but has never run, because it fires on a version tag and none has been cut since it
  merged.
- The only working path is `cd tui && make build`, which needs Go and a clone.
- `installer/scripts/install.sh` implements the right sequence but is gated to Linux and
  fetches the binary from GitHub release assets, a channel nothing publishes to.
- `installer/scripts/install.ps1` has no terminal-hub logic at all.
- `gaia init` 404s on Linux for anyone without Lemonade already installed, and refuses
  to run on macOS.

## Scope decision

Keep the current architecture. The terminal hub stays a Go binary that reaches hub
agents through the Python-launched daemon. We are fixing distribution and setup, not
re-architecting.

This was decided after establishing that the daemon is not optional for the hub path:

- `catalog/agent.go:64-71` defines two transports. `TransportSubprocess` (the zero
  value) spawns a binary directly; `TransportDaemon` relays through the daemon.
- `catalog/catalog.go:528-531` forces `TransportDaemon` for every `FromHub` agent —
  "there is no binary for the TUI to spawn".
- `catalog/hub.go:155-156` makes the hub client itself daemon-backed, so `list`,
  `install`, and `uninstall` all require a daemon, not just running an agent.
- `daemon/client.go:246-253` starts it with `exec.LookPath("gaia")` → `gaia daemon start`.

Absorbing the daemon into Go would mean moving hub operations, sidecar supervision, and
the query relay — the daemon's entire remit. Explicitly out of scope.

## Target journey

```
website  ->  curl -fsSL https://amd-gaia.ai/install.sh | sh      (irm .../install.ps1 | iex)
         ->  uv + Python 3.12 + amd-gaia core + gaia-tui, into ~/.gaia
         ->  gaia init            one-time: Lemonade + model
         ->  gaia-tui             browse hub, install an agent, chat
```

`install.sh` already implements this sequence (`install_uv` → `install_gaia` →
`install_tui` → `add_to_path` → next steps). The work is making it correct and
cross-platform, not rewriting it.

## Naming: the binary stays `gaia-tui`

The long-term intent is for `gaia` to name the Go binary and the legacy Python CLI to
become `gaia-dev`. That rename is **blocked by this architecture** and must not be
attempted in isolation: `daemon/client.go:248` resolves `gaia` on PATH expecting the
*Python* CLI. If the Go binary took that name it would find itself and invoke a
`daemon` subcommand it does not implement.

So `gaia-tui` is not merely the easier option — it is what the current design requires.
The rename becomes possible only once the Go binary owns the daemon's work, which this
spec excludes. Anyone picking up the rename must change that call site in the same
change.

## Work items

### 1. Repair Lemonade acquisition

`lemonade_installer.py:247` builds `lemonade-server_{version}_amd64.deb`. Upstream
renamed the asset; verified against `v11.5.0`:

| Constructed / actual | Result |
| --- | --- |
| `lemonade-server_11.5.0_amd64.deb` (ours) | 404 |
| `lemonade-server_11.5.0-debian13_amd64.deb` | 200 |
| `Lemonade-11.5.0-Darwin.pkg` | 200 |

- Correct the Linux name; add arm64 Linux (upstream publishes it, we ignore it).
- Add the Darwin branch — `get_download_url`, `get_installer_filename`, and both
  install-invocation sites. macOS installs via `installer -pkg <path> -target /` and
  needs elevation; state that requirement rather than prompting silently.
- **Root cause:** a hardcoded filename pattern with no verification. Assert the
  constructed URL resolves and fail with the URL, the version, and where to find the
  real asset list. The next upstream rename must break CI, not a user's terminal.
- Tests: unit coverage of URL shape per (platform, arch), plus one network test that
  resolves the real URLs for the pinned `LEMONADE_VERSION`. A mocked HTTP layer proves
  only that we called it, not that the call is valid — that is why this shipped broken.

Fedora RPMs are out of scope.

### 2. Make the binary obtainable

- Feed the channel `install_tui()` reads. Hub R2 is canonical, since
  `release_components.yml` already publishes all six platform binaries there. Repoint
  `install_tui()` at the hub rather than duplicating artifacts to release assets.
- Lift the Linux-only gate in `detect_environment()`. `install_tui()` already handles
  `darwin/amd64` and `darwin/arm64`; that code is unreachable behind the gate.
- Add terminal-hub installation to `install.ps1`, which currently has none, for
  `win-x64` and `win-arm64`.
- Publish the component: bump both `hub/components/*/gaia-agent.yaml` to the release
  version and let `release_components.yml` run on the tag. Its version gate hard-fails
  on a mismatch by design, because R2 paths are immutable. **Gated on 2a below — do not
  tag until the host API contract is satisfied**, or the published binary cannot talk to
  the core it declares as its minimum.

Elevation is unavoidable — the `.deb` needs sudo, the MSI needs UAC, the `.pkg` needs
sudo. Say so before prompting.

### 2a. The component cannot publish yet — host API contract break

Found after this spec was first written, and it gates work item 2.

The terminal hub needs daemon host API **v1.1+** (`tui/internal/daemon/instance.go:161`).
No released core provides it:

| Source | `DAEMON_API_VERSION` |
| --- | --- |
| repo `main` (`src/gaia/daemon/constants.py:23`) | `1.1` |
| released 0.22.0 wheel, the latest release | `1` |

Both component manifests declare `min_gaia_version: "0.22.0"`. So publishing today ships
a binary that cannot talk to the core version it names as its own minimum — dead on
arrival for anyone who installs the declared requirement. Reproduced: a source-built
terminal hub against an installed 0.22.0 core fails preflight at 0 of 5 with "the running
background service speaks host API v1, which this build cannot use".

It is invisible to developers, whose editable install already serves 1.1. That is why it
survived review, and it is the same fresh-versus-warm asymmetry as the Lemonade 404.

Two consequences:

- **Do not tag a component release until a core release ships API 1.1**, or until
  `min_gaia_version` names a version that does.
- `min_gaia_version` is a hardcoded string with nothing checking it against the real
  contract. A test comparing two constants in the same tree cannot catch this, because
  the break is between the tree and a *published artifact*. The guard must reason about
  the released core's value.

### 3. macOS code signing

Gatekeeper quarantines an unsigned downloaded binary: "cannot be opened because the
developer cannot be verified." Signing and notarizing the terminal-hub binary requires
an Apple Developer identity in CI. Tracked as its own piece of work; the macOS path is
not usable without it.

### 4. Correct the website's platform claims

- `DownloadButton.astro:46` matches `/Mac/i` with no architecture check, then
  deep-links `pick(/arm64\.dmg$/)`. `electron-builder.yml:143-147` builds arm64 only, so
  an Intel Mac visitor is handed a DMG that cannot execute — Rosetta translates x64 to
  arm, not the reverse. Gate on architecture, fall through to the releases page, and
  label the entry point "macOS (Apple Silicon)".
- Repoint `EntryPoints.astro:29` off the CLI-reference anchor to the install command or
  the hub component page, once the component is published.

### 5. Unblock the first agent install

`hub/agents/email/python/gaia-agent.yaml` omits `security_tier`, and the default is the
most restrictive value (`manifest.py:69`, `installer.py:98`, `manifest.ts:196`). The live
catalog therefore reports `experimental` for AMD's own agent, which means
`gaia agent install email` errors without `--trust`, and `/hub/email` tells the visitor
to "review the source before installing".

This lands in the middle of the newcomer journey: the first agent they try to install is
refused. Set `security_tier: verified` and republish. `catalog.ts:90` resolves the
manifest value authoritatively on a new-latest publish, so the edit takes effect —
note that `docs/guides/hub-publishing.mdx:187` claims otherwise and needs correcting.

## Error handling

Every failure in this path must name what failed, what to do, and where to look, per
the no-silent-fallbacks rule:

- A missing upstream asset names the constructed URL and the version.
- An unsupported architecture names the architecture and what is available. It must not
  substitute a different one.
- A failed terminal-hub download must fail the install once the website promises it.
  The current `return 0` soft-skip is acceptable only while the binary is unadvertised.
- An unsupported platform says so plainly instead of offering a path that cannot work.

## Testing

- Unit: URL shape per platform and architecture; the unsupported-arch error.
- Network, CI-gated: the real upstream URLs resolve for the pinned version.
- Fresh-state manual: on a machine with no Lemonade and no `~/.gaia`, run the one-liner
  through to a first agent reply. A warm machine passes while a cold one fails, which is
  how both the Lemonade 404 and the empty binary channel survived review.
- Per platform: Linux x64 and arm64, Windows x64, macOS arm64.

## Verification status

Each fix was checked against its own code, not against a claim.

Verified working:

- Lemonade acquisition — all five platform URLs probed against the real upstream v11.5.0
  release: Linux x64 and arm64 200, macOS arm64 200, macOS x86_64 raises with a named
  reason, Windows 200. The URL that 404'd is fixed.
- Terminal hub help names whichever binary was invoked — built and run as both
  `gaia-tui` and `gaia`.
- The cold-state setup hint leads with the one-line installer; exit 1, message on
  stderr, stdout empty.
- `security_tier: verified` declared and parsing; the publishing guide's contradictory
  claim replaced.
- Browse hub, background service, model resolution, and agent start — verified against a
  live daemon serving host API 1.1.

Not verified:

- **A real agent answer.** Blocked by the sidecar defect below.

## Known defect blocking the last step

The published email sidecar 0.5.0 (`apiVersion 2.4`) passes `_wait_for_health`, then
stops listening while its process stays alive. Reproduced twice on independent fresh
daemons, with the local model server alive throughout, so it is not an artifact of the
model server dying. `lsof` shows zero LISTEN sockets on the port the daemon recorded, and
shutdown needs SIGKILL after a 5s grace.

Consequence: `runMailboxCheck` (`tui/internal/ui/preflight/check.go:723`) relays
`GET /v1/<agent>/connectors` to a sidecar that never answers, so preflight stops at 4 of
5 and no email query can run.

Two supervision gaps belong to this repo regardless of the sidecar's own fault:

- Health is checked once at startup and never re-checked, so the daemon reports "running"
  for a process serving nothing.
- The `Mailbox` row blames a mailbox when the agent itself is unreachable. An unreachable
  sidecar belongs upstream of that row, since the check walk is documented to stop at the
  first failure precisely so later rows do not mislead.

Separately: `EmailConfig()` always attaches `MailboxCheck()`, but triage runs on the local
model alone. A user with no email connector cannot try the headline feature even when the
sidecar is healthy.

## A note on measurement

`~/.gaia` and the daemon are machine-global singletons. Git worktree isolation gives file
isolation, not process or state isolation — two agents running `gaia daemon start/stop`
destroy each other's results silently. Any daemon-derived reading taken while another
process held the daemon must be discarded, not reported. Serialise daemon access.

## Out of scope

- Absorbing the daemon into the Go binary.
- The `gaia` / `gaia-dev` rename. Blocked by `daemon/client.go:248`, which resolves
  `gaia` on PATH expecting the Python CLI: the Go binary cannot take that name while it
  shells out to it.
- Fedora RPM support.
- Intel Mac support for the Agent UI — there is no x64 build; this spec only stops the
  site offering one.

## Open questions

- Does macOS need to ship in the same release as Linux and Windows, or can it follow
  once signing is in place?
- Is Linux arm64 a supported target, or explicitly unsupported despite the upstream
  asset existing?
- Which core release will first ship host API 1.1, and does the component release wait
  for it or ship with a corrected `min_gaia_version`?
