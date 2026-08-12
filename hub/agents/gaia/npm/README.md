# @amd-gaia/gaia

One command gets you a working GAIA:

```bash
npx @amd-gaia/gaia
```

That fetches the two binaries GAIA needs — the agent sidecar and the terminal UI —
verifies both against a checksum manifest that ships inside this package, and drops
you into the terminal UI. No Python to install, no repo to clone, no build step.
Everything runs locally on your machine; nothing you type or index leaves it.

## What it actually does

1. **Resolves your platform** — `win32-x64`, `darwin-arm64`, `darwin-x64`,
   `linux-x64` (plus `linux-arm64` / `win32-arm64` for the terminal UI).
2. **Reads `binaries.lock.json`**, the checksum manifest published with this exact
   package version.
3. **Downloads and SHA-256 verifies both binaries.** A hash that does not match is
   a hard failure — the download is deleted and the run stops. There is no
   "continue anyway" path and no unverified fallback.
4. **Launches the terminal UI**, which brings up the GAIA daemon and the agent
   sidecar and hands you the chat. Its exit code becomes ours.

## Requirements

- **Node.js 18+** (for the built-in `fetch`).
- **[Lemonade Server](https://amd-gaia.ai/docs/reference/dev)** running locally —
  it hosts the model the agent thinks with. GAIA tells you if it isn't up.
- The `gaia` Python CLI on `PATH` for the daemon the terminal UI starts. Install it
  with `curl -fsSL https://amd-gaia.ai/install.sh | sh` (Windows:
  `irm https://amd-gaia.ai/install.ps1 | iex`).

## Supported platforms

The terminal UI is Go and cross-compiles everywhere. The agent sidecar is a frozen
Python build, produced on the machine it targets, and **has no arm64 Linux or arm64
Windows build**. On those two platforms `npx @amd-gaia/gaia` stops with an error
naming your platform and the ones that do work — it will not start a UI with no
agent behind it.

| Platform key   | Agent sidecar | Terminal UI |
| -------------- | :-----------: | :---------: |
| `win32-x64`    |       ✅      |     ✅      |
| `darwin-arm64` |       ✅      |     ✅      |
| `darwin-x64`   |       ✅      |     ✅      |
| `linux-x64`    |       ✅      |     ✅      |
| `linux-arm64`  |       —       |     ✅      |
| `win32-arm64`  |       —       |     ✅      |

`npx @amd-gaia/gaia version` prints this matrix for the version you have installed.

## Commands

```
gaia [run] [options] [-- <tui args>]   Fetch + verify both binaries, then launch the TUI
gaia fetch [options]                   Download + verify only; print JSON and exit
gaia serve [options]                   Run the agent sidecar alone (REST API, no TUI)
gaia version                           Print the lock manifest and this host's platform
gaia help                              Show help
```

Anything after a bare `--` goes to the terminal UI untouched:

```bash
npx @amd-gaia/gaia -- --debug
```

Common options:

| Flag                  | Meaning                                                       |
| --------------------- | ------------------------------------------------------------- |
| `--base-url <url>`    | Override the download base URL from `binaries.lock.json`       |
| `--cache-dir <dir>`   | Where to cache the terminal UI binary                          |
| `--sidecar-dir <dir>` | Where to install the agent sidecar (default `~/.gaia/agents/gaia`) |
| `--platform <key>`    | Fetch for another platform (`fetch` only)                      |
| `--force`             | Re-download even when a verified binary is already cached      |
| `--port <n>`          | Sidecar bind port for `serve` (default `8141`)                 |

Set `DEBUG=gaia` for download, spawn, and sidecar output on stderr. Diagnostics
never touch stdout, which the terminal UI owns.

## Where things land

| What            | Path                                     |
| --------------- | ---------------------------------------- |
| Agent sidecar   | `~/.gaia/agents/gaia/gaia-agent[.exe]`   |
| Terminal UI     | `~/.gaia/npm-cache/gaia-<version>/gaia-tui[.exe]` |

The sidecar goes into the GAIA daemon's own cache directory on purpose: the daemon
is what spawns and supervises it, and it does its own SHA-256 check on the way. By
putting an already-verified binary there we save a second download rather than
racing one.

The terminal UI is installed as `gaia-tui`, **never** as `gaia` — a file named
`gaia` in a cache directory would shadow the `gaia` shim npm puts on your `PATH`.

## Ports

| Service       | Port                              |
| ------------- | --------------------------------- |
| Agent sidecar | `8141` on `127.0.0.1`             |
| GAIA daemon   | assigned at start, recorded in `~/.gaia/host/instance.json` |

Port **4001 is reserved repo-wide** and is refused with an error if you pass it.
Both services bind loopback only — this agent speaks for your documents and memory
and has no business on a LAN interface.

## Running the sidecar on its own

`gaia serve` skips the terminal UI and gives you the REST surface directly, for
integrating GAIA into your own app:

```bash
npx @amd-gaia/gaia serve --port 8141
curl http://127.0.0.1:8141/health
```

It waits for `GET /health`, checks the contract version, and tears the whole
process tree down on Ctrl+C. See [`SPEC.md`](./SPEC.md) for the endpoints.

## Programmatic use

```ts
import { randomUUID } from "node:crypto";
import { fetchAll, startSidecar, shutdown } from "@amd-gaia/gaia";

const { sidecar } = await fetchAll();               // both binaries, SHA-256 verified
const proc = await startSidecar({ binaryPath: sidecar.binaryPath });

const res = await fetch(`${proc.baseUrl}/v1/gaia/query`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ query: "summarize my notes", run_id: randomUUID(), context: [] }),
});

await shutdown(proc);
```

`/v1/gaia/query` streams Server-Sent Events terminated by exactly one `final` or
`error`. `fetchAll()` also returns the TUI's path if you would rather launch that.

Every failure throws a typed error (`IntegrityError`, `PlatformError`,
`HealthTimeoutError`, `VersionMismatchError`, `BinaryNotFoundError`) with a message
that names what failed and what to do about it.

## Integrity

`binaries.lock.json` is the single source of truth for what gets downloaded and
what it must hash to. It is regenerated by the release pipeline from the artifacts
it just built and published.

Between releases the lock carries `PENDING-replace-with-real-sha256` placeholders.
**A placeholder blocks the fetch outright** — before any network call — so an
unverifiable binary can never be downloaded, let alone executed. If you need to run
against a locally built binary, build it yourself and point the lifecycle helpers
at it directly; the fetcher will not be talked into it.

## Links

- Guide: <https://amd-gaia.ai/docs/guides/gaia>
- Technical reference: [`SPEC.md`](./SPEC.md)
- Changes: [`CHANGELOG.md`](./CHANGELOG.md)
- Issues: <https://github.com/amd/gaia/issues>

MIT licensed. © 2024-2026 Advanced Micro Devices, Inc.
