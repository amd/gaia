# @amd-gaia/gaia

One command gets you a working GAIA:

```bash
npx @amd-gaia/gaia
```

That fetches the two binaries GAIA needs — the agent sidecar and the terminal UI —
verifies both against a checksum manifest that ships inside this package, and drops
you into the terminal UI. No Python to install, no repo to clone, no build step.
Everything runs locally on your machine; nothing you type or index leaves it.

The terminal UI you get here is the published **`terminal-hub`** component — the
exact same binary a full GAIA install runs as `gaia tui`, not a separate build. So
however you arrive at the terminal UI, it behaves identically.

## What it actually does

1. **Resolves your platform** — `win32-x64`, `darwin-arm64`, `darwin-x64`,
   `linux-x64` (plus `linux-arm64` / `win32-arm64` for the terminal UI).
2. **Reads `binaries.lock.json`**, the checksum manifest published with this exact
   package version. It records, per binary, which hub lane it comes from and what
   it must hash to.
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

`npx @amd-gaia/gaia version` prints this matrix, plus the version and source URL of
each binary, for the release you have installed.

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

| Flag                  | Meaning                                                       | Accepted by |
| --------------------- | ------------------------------------------------------------- | ----------- |
| `--base-url <url>`    | Override the download base URL from `binaries.lock.json`. Must be `https:` | `run`, `fetch`, `serve` |
| `--allow-insecure-base-url` | Permit a non-`https` `--base-url` (a trusted local mirror) | `run`, `fetch`, `serve` |
| `--sidecar-dir <dir>` | Where to install the agent sidecar (default `~/.gaia/agents/gaia`) | `run`, `fetch`, `serve` |
| `--cache-dir <dir>`   | Where to cache the terminal UI binary                          | `run`, `fetch` |
| `--force`             | Re-download even when a verified binary is already cached      | `run`, `fetch`, `serve` |
| `--platform <key>`    | Fetch for another platform                                     | `fetch` |
| `--component <name>`  | Fetch only `sidecar` or `tui`                                  | `fetch` |
| `--port <n>`          | Sidecar bind port (default `8141`)                             | `serve` |

A flag a command does not read is **refused**, not ignored — `gaia run --port
9000` exits 2 rather than silently coming up on the default port.

Set `DEBUG=gaia` for download, spawn, and sidecar output on stderr. Diagnostics
never touch stdout, which the terminal UI owns.

## Where things land

| What            | Path                                     |
| --------------- | ---------------------------------------- |
| Agent sidecar   | `~/.gaia/agents/gaia/gaia-agent[.exe]`   |
| Install record  | `~/.gaia/agents/gaia/.installed`         |
| Terminal UI     | `~/.gaia/npm-cache/gaia-<version>/gaia-tui[.exe]` |

The sidecar goes into the GAIA daemon's own cache directory on purpose: the daemon
is what spawns and supervises it, and it does its own SHA-256 check on the way. By
putting an already-verified binary there we save a second download rather than
racing one.

The `.installed` record next to it is what the daemon and the terminal UI read to
know the agent is installed — without it the UI would run the sidecar as its own
stdio child instead of letting the daemon supervise it. It is rewritten even when
the binary was already cached, so an install left by an earlier version repairs
itself the next time you run. A `--platform` fetch stages a binary for a
*different* machine, so it deliberately leaves no record.

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

const sessionId = randomUUID(); // reuse across the whole conversation, see below

const res = await fetch(`${proc.baseUrl}/v1/gaia/query`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    query: "summarize my notes",
    run_id: randomUUID(),
    session_id: sessionId,
    context: [],
  }),
});

await shutdown(proc);
```

`/v1/gaia/query` streams Server-Sent Events terminated by exactly one `final` or
`error`. `fetchAll()` also returns the TUI's path if you would rather launch that.

**Reuse the same `session_id` for every turn in a conversation.** It is what
lets a document you had it index, or a skill you had it load, survive to the
next question — drop it (or mint a new one per call) and the agent still
answers, but it forgets everything from the previous turn. See
[`SPEC.md` §5.2](./SPEC.md#52-session_id-and-agent-retention) for the retry
and eviction behavior.

Every failure throws a typed error (`IntegrityError`, `PlatformError`,
`HealthTimeoutError`, `VersionMismatchError`, `BinaryNotFoundError`) with a message
that names what failed and what to do about it.

## Where the binaries come from

The two binaries ship from two different places, and `binaries.lock.json` records a
version and a source URL for each:

| Binary        | Published as                              | Built by                         |
| ------------- | ----------------------------------------- | -------------------------------- |
| Agent sidecar | the `gaia` agent, at this package's version | this package's release           |
| Terminal UI   | the `terminal-hub` component, at its own version | the core GAIA release       |

The terminal UI is **consumed, not rebuilt**. It is byte-for-byte the `gaia tui`
binary a core install ships, so there is no second copy that could lag behind or
behave differently — which is the entire reason it is sourced this way.

## Integrity

`binaries.lock.json` is the single source of truth for what gets downloaded and
what it must hash to. The release pipeline regenerates it from the artifacts
actually being served — the sidecars it just published, and the terminal-hub
artifacts it downloaded and cross-checked against the hub's own recorded hashes.

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
