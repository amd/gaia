# Shipping a custom build someone else can run

## Why this matters

You cannot hand a colleague a build of GAIA today. To try a branch they need
Python, uv, Go, a clone, `uv pip install -e`, a `go build`, and an environment
variable nobody would guess. That is a toolchain, not a handover — and it means
every internal demo, bug repro and design review costs the other person an hour
before they see anything.

The goal: **one `.exe` you can send.** It installs the terminal UI and the agent,
and leaves only Lemonade and the models to `gaia init` — those are gigabytes and
belong on the model server, not in a build artifact.

## What already works

The hard part is done, and it is closer than it looks. The flagship is **not**
behind the daemon:

```go
// The flagship, spawned directly as a child process: TUI -> agent ->
// Lemonade, with no daemon, HTTP port, bearer token or model-slot lease
// in the path.
Transport:  TransportSubprocess,
BinaryPath: "gaia-agent",
```
<sub>`tui/internal/catalog/catalog.go`</sub>

So the runtime shape is already two binaries talking over stdin/stdout. The
release pipeline already builds and publishes both — `binaries.lock.json`
declares a `sidecar` component and a `tui` component, per platform.

## The blocker — RESOLVED

**The frozen agent binary speaks the wrong protocol.** Its entry is
`packaging/server.py`, which re-exports `gaia_agent.server.main()` — a uvicorn
HTTP server. The TUI spawns it expecting newline-delimited JSON. Verified on a
binary built from this branch:

```
$ gaia-agent.exe --json-events
usage: gaia-agent.exe [-h] [--host HOST] [--port PORT]
gaia-agent.exe: error: unrecognized arguments: --json-events
```

The stdio transport exists and is complete — `gaia_agent/stdio.py`, whose own
docstring says the TUI's argv spellings are load-bearing:

> The TUI appends `--use-claude` / `--claude-model` as literal strings to the
> child argv, so these exact spellings are load-bearing.

It was simply never frozen — `freeze.py` had one `ENTRY`, the HTTP server.

**Fixed.** `gaia_agent.server.main()` now dispatches on argv: `--serve` (or a
bind flag, which the daemon passes without `--serve` for the *email* sidecar)
runs HTTP, anything else is the stdio wire. One binary, both transports.
Verified on a real build: it answers JSONL, and `--serve` still passes
`smoke_test.py`.

A second fault surfaced only after that: the frozen binary had **no speaker
identification at all**, because the engine was pip-installed lazily and
`sys.executable` inside PyInstaller is the `.exe`, not an interpreter. A
46-minute meeting produced zero speaker spans from the frozen build where a
source checkout produced four. sherpa-onnx is now bundled at build time behind
a `diarize` extra, guarded by `_verify_collect_targets` so a missing engine
fails the build rather than shipping silently. Binary: 92 -> 102.5 MB.

## Four things to build

### 1. Make one binary serve both transports

`gaia-agent` should dispatch on argv rather than shipping two executables:
no `--host`/`--port` means stdio JSONL (what the TUI spawns), `--serve` means
the HTTP sidecar (what the daemon spawns for the email agent and the Agent UI).

One binary, because two would double the artifact matrix and give the installer
a choice it has no basis to make. The dispatch belongs in
`gaia_agent.server.main()` so the frozen entry and `python -m` agree.

**Done when** `gaia-agent --json-events` answers a JSONL turn, `gaia-agent
--serve` still passes `smoke_test.py`, and the TUI drives the frozen binary with
no `GAIA_GAIA_AGENT_MODE` set.

### 2. A local bundle target

`make bundle` (or `packaging/bundle.py`) that produces a directory with the
frozen agent, the TUI binary, and a launcher — from the working tree, not from
the hub. This is what makes a *custom* build shareable, and it is what CI would
call for the release path too.

**Done when** the output runs on a machine with no Python, no Go, and no clone.

### 3. The installer

`installer/nsis/` has an icon, a sidebar bitmap and an `installer.nsh` — no
script that packages anything. It needs one that lays down both binaries, puts
`gaia-tui` on PATH, writes the install sentinel `ResolveExecutable` looks for,
and offers a desktop entry.

macOS and Linux want the equivalent (`.pkg`, `.deb`) but Windows is where the
colleagues are; do that first and do not block on the others.

**Done when** double-clicking the `.exe` on a clean Windows box ends with a
working `gaia-tui` on PATH.

### 4. Decide who installs Lemonade

The TUI's preflight already detects Lemonade and can pull models, but when
Lemonade itself is missing it tells the user to run `gaia init` — the Python CLI
the bundle deliberately does not ship.

Two honest options:

- **Teach preflight to install it.** It already owns the detection and the
  progress UI; installing is a platform package step (`winget` / `.deb` /
  `.pkg`). Fully removes Python from the first-run path.
- **Ship the one-line install script.** The installer drops
  `installer/scripts/install.ps1`, which already knows how to set up a venv and
  Lemonade, and preflight points at it.

The first is the better product and the larger change. Pick deliberately rather
than defaulting.

## Sequencing

Items 1 and 3 are done: the binary serves both transports, and
`installer/scripts/build-gaia-installer.ps1` produces a per-user Windows
installer (~107 MB) that lays down both binaries, puts `gaia-tui` on PATH and
writes the install sentinel. Verified by installing and transcribing from the
installed binaries.

Item 2 (a local `bundle` target) and item 4 (who installs Lemonade) remain.
Neither blocks sharing a build today — the installer covers Windows, which is
where the colleagues are.

One caveat learned the hard way: on a machine that already has the Python
package, `gaia-agent` on PATH **shadows** the installed binary, because
`ResolveExecutable` searches PATH first. That is correct for developers and
invisible to end users, but it means the installer cannot be meaningfully
tested on a dev box without moving the console script aside first.

## What is explicitly out of scope

Bundling Lemonade or the models. A GGUF is gigabytes, it changes independently
of GAIA, and `gaia init` already handles it. The bundle's job ends at "the
agent and the UI are on this machine and talk to each other".
