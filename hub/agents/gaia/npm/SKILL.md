---
name: integrate-gaia
description: Use when integrating the @amd-gaia/gaia npm package — running GAIA's flagship agent, or embedding its local sidecar into a Node, TypeScript, or Electron app. Covers the one-command path, the SHA-256 integrity gate, platform coverage, starting the sidecar, the /v1/gaia/query SSE contract, and the gotchas that will bite you.
---

# Integrating `@amd-gaia/gaia`

`@amd-gaia/gaia` delivers **two binaries** and owns their process lifecycle: the
frozen **agent sidecar** (`gaia-agent`) and the Go **terminal UI** (`gaia-tui`).
It ships no agent logic of its own, and it **builds neither binary at install
time** — both are published artifacts it downloads and verifies. The terminal UI
is the published `terminal-hub` component, the same binary a full GAIA install
runs as `gaia tui`, so its behaviour cannot differ from that one. Everything runs on the local machine against
a local model server — nothing you type or index leaves it.

Two ways in:

- **`npx @amd-gaia/gaia`** — fetch, verify, launch the terminal UI. What a human runs.
- **The programmatic exports** — fetch, spawn the sidecar, drive `POST /v1/gaia/query`
  yourself. What you use when embedding GAIA in an app.

> **This file is NOT one of the agent's own skills.** It is the integration
> playbook: how *you* wire this package into an app. The agent separately loads
> **Agent Skills** into its own prompt at runtime from
> `gaia_agent/skills/<name>/SKILL.md`. Same filename, different artifact —
> don't ship this one as an agent skill. See
> [Skills](#10-skills--one-always-on-the-rest-opt-in).

## 1. Install

```bash
npx @amd-gaia/gaia          # no install step; fetches what it needs on first run
npm install @amd-gaia/gaia  # when you want the programmatic exports
```

The package is **ESM-only** (`"type": "module"`) and needs **Node 18+** for the
built-in `fetch`. Use `import`, not `require`; from CommonJS use
`await import("@amd-gaia/gaia")`.

> `@amd-gaia/gaia` **publishes with this release** — it is not on npm yet. Until
> the release tag lands, `npx @amd-gaia/gaia` will not resolve. Run the agent from
> a source checkout in the meantime (see
> [the guide](https://amd-gaia.ai/docs/guides/gaia)).

## 2. What `npx @amd-gaia/gaia` actually does

1. Resolves the host platform key (`` `${process.platform}-${process.arch}` ``).
2. Reads `binaries.lock.json`, the checksum manifest published with this exact
   package version. Each component records its own hub lane, version, artifact
   name and hash — they do not share a base URL.
3. Downloads **both** binaries, each from its own lane, and **SHA-256 verifies
   each against the lock**.
4. Installs the sidecar into `~/.gaia/agents/gaia/` and the TUI into
   `~/.gaia/npm-cache/gaia-<version>/`.
5. Writes `~/.gaia/agents/gaia/.installed` — the record the daemon and the TUI
   both read to decide the sidecar is installed. Written on a cache hit too, so
   an install staged by an earlier release repairs itself.
6. Execs the TUI, whose exit code becomes ours.

`run` deliberately does **not** spawn a sidecar. The TUI reaches agents through
the GAIA daemon's relay and never holds a sidecar token, and the daemon is what
spawns and supervises the sidecar — from exactly the directory step 4 wrote to.
A second sidecar started here would only fight the daemon's own for port 8141. Use
`gaia serve` when you want to own the process.

Other commands: `gaia fetch` (download + verify, print JSON, exit),
`gaia serve` (sidecar alone), `gaia version` (per-component version, source URL,
and platform matrix). Anything after a bare `--` goes to the TUI verbatim.

### Where each binary comes from

| Component | Hub lane                                  | Artifact names            |
| --------- | ----------------------------------------- | ------------------------- |
| `sidecar` | `agents/gaia/<agentVersion>/`             | `gaia-agent-<platformKey>[.exe]` |
| `tui`     | `agents/terminal-hub/<componentVersion>/` | `gaia-<goPlatform>[.exe]` |

Two things follow from that, and both bite if you assume otherwise:

- **The two components version independently.** `lock.agentVersion` is this
  package's version; `components.tui.componentVersion` is the terminal-hub release
  it consumes. They will not match.
- **The TUI's artifact names use `win-x64` / `win-arm64`, not `win32-*`.** Platform
  *keys* stay in Node's namespace (`process.platform` says `win32`); only the
  `filename` crosses over. Never build a TUI URL by interpolating a platform key —
  read `filename` from the lock entry.

## 3. The integrity gate — it will stop you, by design

The SHA-256 check is this package's security boundary, and there is no flag,
env var, or option that relaxes it.

- Bytes are hashed **in memory and compared before anything is written** to the
  cache path. A mismatch raises `IntegrityError` naming expected vs actual and
  leaves nothing on disk.
- A **placeholder hash blocks the fetch before any network call** — between
  releases every `sha256` in the lock is `PENDING-replace-with-real-sha256`, and
  a value that is all zeros or contains `PENDING` (case-insensitive) is treated
  as a placeholder. You get a `PlatformError`, not a download.
- A cache hit **re-hashes the on-disk file**. A cached binary whose bytes drifted
  is re-downloaded, not reused.

If you need to run against a locally built binary, build it and point
`startSidecar` / `runTui` at it directly. The fetcher will not be talked into it.

## 4. Platform coverage — the sidecar has two gaps

`terminal-hub` publishes the TUI for all six targets. The sidecar is a PyInstaller
freeze built on the platform it targets, and there is **no arm64 Linux and no arm64
Windows sidecar build**.

| Platform key   | Sidecar | TUI |
| -------------- | :-----: | :-: |
| `win32-x64`    |   yes   | yes |
| `darwin-arm64` |   yes   | yes |
| `darwin-x64`   |   yes   | yes |
| `linux-x64`    |   yes   | yes |
| `linux-arm64`  | **no**  | yes |
| `win32-arm64`  | **no**  | yes |

Resolving the sidecar on those two keys raises `PlatformError` naming the
platform and the supported set. It is not silently skipped, and the TUI is never
launched with no agent behind it. `npx @amd-gaia/gaia version` prints the matrix
for the version you have.

## 5. Prerequisite — a local Lemonade server

The agent thinks with a model hosted by **Lemonade Server**, which this package
does not install. Required before any query succeeds:

1. Lemonade **10.2.0 or newer**, running (`lemonade-server serve`).
2. The default model downloaded (`gaia download Gemma-4-E4B-it-GGUF`, or
   `gaia init`).

Do not guess — ask the sidecar. `GET /v1/gaia/init` is a read-only preflight
(it never pulls or loads) that probes Lemonade, compares its version to the
floor, and checks the model is present:

```bash
curl -s http://127.0.0.1:8141/v1/gaia/init
```

It answers **200** when ready and **503** when not, with the **same body shape
either way** — so branch on `.ready` and render `.hint`, never on the status code
alone:

```jsonc
{
  "ready": false,
  "lemonade": { "reachable": false, "base_url": "…", "version": null,
                "min_version": "10.2.0", "compatible": null },
  "model":    { "id": "Gemma-4-E4B-it-GGUF", "present": false,
                "loadable": null, "ctx_size": null },
  "hint": "Local Lemonade Server is not reachable at … — start it with `lemonade-server serve`, or set LEMONADE_BASE_URL to a running server."
}
```

`lemonade.compatible: null` is **indeterminate**, not a pass — the version could
not be parsed. Render it as unknown.

`GET /health` is liveness only. A green `/health` means the REST surface is up;
it says nothing about whether a query will work.

## 6. Start the sidecar

```ts
import { fetchAll, startSidecar, shutdown } from "@amd-gaia/gaia";

// Fetch + SHA-256 verify both binaries. Build step, not per request.
const { sidecar, tui } = await fetchAll();

// Spawn -> poll /health -> check the contract apiVersion, in one call.
const proc = await startSidecar({ binaryPath: sidecar.binaryPath });  // port 8141

// ... drive proc.baseUrl ...

await shutdown(proc);   // tree-kill; auto-cleanup also reaps on exit
```

- `fetchAll(opts?)` returns `{ sidecar, tui, lock }`. Each result carries
  `binaryPath`, `platformKey`, `sha256`, `cached`, `url`. For one component use
  `fetchBinary({ component: "sidecar" | "tui", outDir })`.
- `startSidecar` throws if the binary can't start, never becomes healthy
  (`HealthTimeoutError`, 60 s default — a cold one-file build unpacks first), or
  reports an `apiVersion` whose **major** differs from this package's
  (`VersionMismatchError`). On any failure it shuts the sidecar down before
  rethrowing, so a failed start never leaks a process.
- **Tree-kill is not optional.** The frozen sidecar spawns a child uvicorn
  process that `child.kill()` on the parent does not reap, which leaves port 8141
  bound. `shutdown` kills the group (POSIX `SIGTERM` to `-pid`, escalating to
  `SIGKILL`; Windows `taskkill /T /F`). `autoCleanup` (default `true`) also reaps
  on `exit`, `SIGINT`/`SIGTERM`/`SIGHUP`, `uncaughtException`, and
  `unhandledRejection`. A `SIGKILL` of *your* process is the one case nothing
  in-process can catch.
- **Mint a caller token, or you are running the sidecar unauthenticated.** The
  sidecar requires `Authorization: Bearer <token>` on every `/v1/gaia/*`
  request, and skips the check only when neither token env var is set — dev
  mode, which is what `startSidecar` gives you, because this package mints
  nothing. Loopback binding is not the boundary: this agent has shell and file
  tools. Mint your own and pass it through the `env` option (`StartOptions`
  extends `SpawnOptions`, so it merges over `process.env`):

  ```ts
  import { randomBytes } from "node:crypto";
  const token = randomBytes(32).toString("hex");
  const proc = await startSidecar({
    binaryPath: sidecar.binaryPath,
    env: { GAIA_GAIA_SIDECAR_TOKEN: token },   // or ..._TOKEN_FILE, a 0600 path
  });
  ```

  Then send `authorization: "Bearer " + token` on every `/v1/gaia/*` call.
  `/health`, `/version`, and `/v1/gaia/version` are exempt, so `startSidecar`'s
  own health-and-version handshake works either way. **If you did not spawn the
  sidecar** — you are talking to one the GAIA daemon started — the token is the
  daemon's, delivered to the sidecar as `GAIA_GAIA_SIDECAR_TOKEN_FILE` (a `0600`
  file path); read it from there, don't invent one. A 401 whose `detail` names
  both env vars means you sent the wrong token or none.

Or skip the code entirely and let the CLI own it:

```bash
npx @amd-gaia/gaia serve --port 8141
curl http://127.0.0.1:8141/health
```

## 7. Call `POST /v1/gaia/query`

This is the whole agent surface. There is **no typed query client** in this
package — call it with plain `fetch`. Contract version **2.12**; the stream is
`text/event-stream` terminated by **exactly one** `final` or `error`.

Request body (`extra: "forbid"` — an unknown field is a **422**, not ignored):

| Field | Required | Notes |
|---|---|---|
| `query` | yes | Non-empty. |
| `run_id` | yes | **You mint it**, and it must be a UUID (non-UUID → 422). It is the cancel handle, valid from the instant the request is sent. |
| `context` | yes | Transcript slice, pushed in the body — may be `[]`, never absent. Each item `{ role, content }`; `role` ∈ `user` / `assistant` / `system` / `tool`. |
| `session_id` | no | Contract ≥ 2.12. **Pass it.** The agent persists its indexed-document set per session — without it, it forgets a document between the turn that indexed it and the next question. |
| `can_answer_questions` | no | Set `false` for one-shot / batch runs so the agent resolves ambiguity itself instead of parking on a question nobody can see. |
| `model` | no | Overrides the model id — only when the run builds a fresh agent. On a retained `session_id` the agent already exists, so a model that differs from the one it was built with is a **409**, not an override. |
| `provider` | no | Local inference only — anything but `"lemonade"` is a **400**. |
| `max_steps` | no | ≥ 1. |

```ts
import { randomUUID } from "node:crypto";

const runId = randomUUID();
const res = await fetch(`${proc.baseUrl}/v1/gaia/query`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    accept: "text/event-stream",
    authorization: `Bearer ${token}`,   // required unless the sidecar is in dev mode — §6
  },
  body: JSON.stringify({
    query: "Summarize the PDFs in ~/Documents/reports",
    run_id: runId,
    context: [],
    session_id: "s1",
    can_answer_questions: false,
  }),
});

const reader = res.body!.getReader();
const dec = new TextDecoder();
let buf = "";
outer: for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  let i: number;
  while ((i = buf.indexOf("\n\n")) >= 0) {
    const frame = buf.slice(0, i);
    buf = buf.slice(i + 2);
    const line = frame.split("\n").find((l) => l.startsWith("data: "));
    if (!line) continue;                  // ":" frames are keepalive comments
    const ev = JSON.parse(line.slice(6));
    switch (ev.type) {
      case "status":      console.log(ev.message); break;
      case "token":       process.stdout.write(ev.delta); break;
      case "tool_call":   console.log(`→ ${ev.tool}`, ev.args); break;
      case "tool_result": console.log(`← ${ev.tool}`, ev.data); break;
      case "needs_confirmation": break;   // see §8 — a refusal follows
      case "needs_input": /* answer it — see below */ break;
      case "final":       console.log(ev.answer); break outer;   // terminal
      case "error":       console.error(ev.detail); break outer; // terminal
      default:            console.warn("unsupported event", ev); // future additive type
    }
  }
}
```

The canonical event shapes, as emitted:

| Event | Shape |
|---|---|
| `status` | `{ type, message }` — progress and reasoning narration |
| `token` | `{ type, delta }` — answer text to append |
| `tool_call` | `{ type, tool, args }` |
| `tool_result` | `{ type, tool, data, render? }` |
| `needs_confirmation` | `{ type, run_id, action, summary }` — no `confirm_url`; see §8 |
| `needs_input` | `{ type, run_id, request_id, question, options[], allow_free_text, sensitive, respond_url, timeout_seconds? }` |
| `final` | `{ type, answer, usage? }` — terminal |
| `error` | `{ type, detail, status }` — terminal, surface `detail` verbatim |

Rules a client must respect:

- **An idle run emits `: keepalive` SSE comments every 10 s.** Skip lines that
  aren't `data:` and reset your read-idle timer on them — a long tool call is not
  a dead stream.
- **Never treat stream close without a terminal event as success.** The server
  guarantees one; a close without one means something broke on your side.
- **Answer `needs_input`, don't restart.** The run is parked on the *same*
  stream. `POST /v1/gaia/query/{run_id}/respond` with
  `{ request_id, response }` and keep reading the existing stream — a fresh
  `/query` POST abandons the paused run. Unknown run → **404**; a `request_id` that
  is no longer pending → **409** (both loud, never a silent drop). Render each
  option's `description`, and mask the input when `sensitive` is set.
- **Cancel with `POST /v1/gaia/query/{run_id}/cancel`.** It returns
  `{ run_id, cancelled }` — an unknown id reports `cancelled: false` with a
  **200**, not a 404, because a cancel racing a normal completion is expected.
  Dropping the HTTP connection also cancels the run.

## 8. Over `/v1/gaia/query`, confirmation-gated tools are **refused, not prompted**

Read this before you design a workflow around it. This section is about the HTTP
surface — the agent's other transport can collect an approval; see SPEC §5.5.

Six of the agent's 67 tools mutate the machine and need explicit approval before
they run. Four sit in the base `TOOLS_REQUIRING_CONFIRMATION` set —
**`write_file`**, **`edit_file`**, **`run_shell_command`**, and
**`execute_python_file`** — and the flagship adds two of its own,
**`install_skill`** and **`remove_skill`**, because installing a skill writes
third-party code under `~/.gaia/skills` and removing one deletes it. Everything
else — reading, indexing, querying, web fetching, memory — runs without asking.

Over `/v1/gaia/query` there is **no way to collect an approval**, so the stream
does not prompt. When the agent reaches one of those tools it emits a
`needs_confirmation` event, and the server **immediately follows it with a
terminal `final`** whose `answer` says it stopped before running that action,
then cancels the run. There is no `confirm_url`, no resume, and no
`/query/{run_id}/confirm` endpoint — it is a deliberate deny-by-default stub, not
an oversight.

There is also no way to turn the gating off over HTTP. The stdio transport's
`--bypass-permissions` runs gated tools unasked *and* lifts the shell tool's
guardrails (compound operators, the read-only binary allowlist, the rate limit),
which is arbitrary code execution — appropriate for one local parent on a
private pipe, not for a bound socket. The request body rejects unknown fields,
so a client cannot ask for it. See SPEC §5.5.

Concretely, your client sees:

```
data: {"type":"needs_confirmation","run_id":"…","action":"write_file","summary":"Run 'write_file'?"}
data: {"type":"final","answer":"I stopped before running 'write_file' because it needs your explicit approval, and this streaming surface cannot collect that yet. …"}
```

So: **`/query` cannot run any of those six tools.** If your integration needs
that, drive the agent from a surface that can prompt — its stdio transport is the
one that can, because its control channel carries an approval back to a turn
already in flight (SPEC §5.5) — or perform the mutation yourself from your own
code and let the agent do the reading and reasoning. Treat `needs_confirmation`
as an early warning that the run is about to end, not as a question you can
answer.

## 9. File-access scope

The agent's file, document, and data tools are confined to a set of allowed
paths, and **the default is the user's home directory**. That is the honest scope
for a personal document agent, and it is still a real boundary — system
directories, program files, and other users' homes are refused, with the check
run against the *resolved* path so a symlink out of scope doesn't slip through.

**In 0.1.1 narrowing it is a construction-time setting only.** The packaged
sidecar exposes no flag or env var for `allowed_paths` (its CLI accepts only
`--host` and `--port`), so restricting the scope means embedding `GaiaAgent` in
your own Python process:

```python
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

agent = GaiaAgent(config=GaiaAgentConfig(allowed_paths=["/home/me/Documents"]))
```

## 10. Skills — one always-on, the rest opt-in

The agent is built to host **Agent Skills** (short playbooks loaded into its own
prompt, grouped into named sets, one set active per launch), and its bundled
skill directory is the highest-precedence discovery root.

Loaded skills are **not** all resident every turn: each turn the agent embeds
the query against every loaded skill's description and renders only the
matching bodies in full — the rest collapse to a one-line menu entry, and the
model (or the user) re-activates one by calling `load_skill` on it again.
`GAIA_DYNAMIC_SKILLS=0` disables the per-turn selection (every loaded body
renders every turn); `GAIA_DYNAMIC_SKILLS_TAU=<float>` overrides the match
threshold. Manifest `skills:` entries are always-on and never collapse. If the
embedder is unavailable, selection disables itself for the session and every
body renders — capability is never silently lost to a failed match.

**One skill ships enabled: `gaia-voice`.** It is a manifest `skills:` entry, so
it is always on, always rendered in full, and paid on every LLM call of every
turn — budget for it. It is not a task recipe but the agent's honesty floor: do
not claim work you did not do, do not present empty output as a result, do not
substitute a near-miss and report success. Those failures corrupt an answer
whatever the task is, which is why it cannot live in an opt-in bundle. It
declares no tools, and its body measures 676 tokens (tiktoken `cl100k`).

**No skill *set* loads.** `gaia-agent.yaml` ships its `skill_sets:` and
`default_skill_set:` blocks **commented out** — following the email agent's
precedent, because loading several skill bodies into every prompt costs tokens
and no eval has measured that trade for this agent yet. Re-enabling is
uncommenting two blocks; no code change.

So today there is nothing for `GAIA_SKILL_SET` to select — leave it unset. Once
a release declares sets, `GAIA_SKILL_SET` is the selection channel for the
packaged sidecar (its CLI accepts only `--host` and `--port`), and an undeclared
name raises naming the valid sets rather than falling back to a default. Beyond
`gaia-voice`, do not design around a skill being on by default.

## 11. Ports

| Service | Port |
|---|---|
| Agent sidecar | `8141` on `127.0.0.1` |
| GAIA daemon | assigned at start, recorded in `~/.gaia/host/instance.json` |

Port **4001 is reserved repo-wide**: `spawnSidecar` throws a `RangeError` and
`gaia serve --port 4001` exits 2. Both services bind loopback only — this agent
speaks for the user's documents and memory and has no business on a LAN
interface.

## 12. Running in a server or long-lived app

- **`fetchAll` / `fetchBinary` are a build step**, not per request — network plus
  a full SHA-256 hash of a large artifact. Run once at install time.
- **`resolveSidecarPath` / `resolveTuiPath` are startup, not per request.** They
  re-hash the binary against `binaries.lock.json` before handing back a path that
  gets spawned, so they cost a full read of a large file. Resolve once and keep
  the path. `{ verify: false }` skips the check for a binary you built yourself.
- **Spawn once at boot** and hold the `Sidecar` handle for the process lifetime.
  Never per request.
- **Low concurrency.** One local Lemonade model slot, so parallel queries
  serialize. Cap inflight runs.
- **The package does not restart a crashed sidecar.** It reaps one; supervision
  is the daemon's job (or yours).
- **`DEBUG=gaia`** puts download, spawn, and sidecar output on **stderr**. stdout
  belongs to the TUI once exec'd, and to machine-readable JSON for `fetch` /
  `version` — never write diagnostics there.

Every failure throws a typed error extending `GaiaError`, so
`instanceof GaiaError` catches any of ours: `IntegrityError`, `PlatformError`,
`HealthTimeoutError`, `VersionMismatchError`, `BinaryNotFoundError`, `HttpError`.
There is no silent null.

## Gotchas — read before debugging

- **`/health` green ≠ ready.** It never touches the model server. Use
  `GET /v1/gaia/init` and branch on `.ready`; it returns 503 with a full body and
  a `hint`, not an empty error.
- **A terminal `error` whose `detail` starts "Local Lemonade Server is not
  reachable"** means Lemonade isn't running or isn't reachable — not a bug in
  this package. Start it, or set `LEMONADE_BASE_URL`.
- **`needs_confirmation` is followed by a refusal and the run ends.** See §8.
  The six gated tools are unreachable **over `/query`** — the agent itself can
  run them on a transport that can prompt (SPEC §5.5).
- **A placeholder hash in `binaries.lock.json` blocks the fetch before any
  network call.** Between releases that is the *expected* state — it is not a
  broken install, and there is no override.
- **No `linux-arm64` / `win32-arm64` sidecar.** The TUI has both. A `PlatformError`
  on those hosts is the design, not a missing artifact.
- **A `401` from `/v1/gaia/*` is the caller-auth token, not a bug.** The sidecar
  requires `Authorization: Bearer <token>`; `/health`, `/version`, and
  `/v1/gaia/version` are exempt, which is why a green health check sits happily
  in front of a 401 on `/query`. It skips the check only in dev mode — neither
  token env var set — which is what `spawnSidecar` and `gaia serve` produce,
  because this package mints nothing. Do not treat loopback binding as the
  boundary; mint a token and pass it (§6).
- **`run_id` must be a UUID**, and unknown fields in the request body are a
  **422** — the model forbids extras. Typos don't get ignored.
- **`gaia run` needs the *Python* `gaia` CLI on `PATH`** — the TUI shells out to
  it to start the daemon. So the TUI doesn't re-invoke our own npm shim, the
  child's `PATH` is rewritten: a directory holding nothing but our shim (an npx
  temp dir) is dropped, and a **shared** bin directory is moved to the end
  instead of removed, so the `python3` / `lemonade-server` / real `gaia` beside
  it stay reachable. If the Python CLI isn't installed anywhere, the daemon never
  comes up.
- **The TUI is installed as `gaia-tui`, never `gaia`** — the terminal-hub artifact
  *is* called `gaia-<platform>`, and a file named `gaia` in a cache directory would
  shadow the npm bin shim. The lock's `filename` and `executable` differ for that
  reason. Don't rename it back.
- **The TUI comes from a lane this package doesn't publish.** If a fetch 404s on
  the TUI but not the sidecar, the pinned `terminal-hub` version is the thing to
  check — `gaia version` prints it and its base URL.
- **ESM-only.** `require("@amd-gaia/gaia")` fails; use `import` or dynamic
  `import()`.

## Verify the integration

Green path, in order:

```bash
npx @amd-gaia/gaia version          # per-component version + source URL + matrix
npx @amd-gaia/gaia fetch            # JSON: one entry per binary with its sha256
npx @amd-gaia/gaia serve --port 8141
```

Against a lock that still carries `PENDING-…` hashes, `fetch` is *expected* to
fail with a `PlatformError` before any download — that is the gate working, not a
broken install. Only a published release has real hashes.

Then, in another terminal:

```bash
curl -s http://127.0.0.1:8141/health          # {"status":"ok","service":"gaia-agent-gaia"}
curl -s http://127.0.0.1:8141/version         # {"apiVersion":"2.12","agentVersion":"0.1.1"}
curl -s http://127.0.0.1:8141/v1/gaia/init    # 200 + "ready":true, or 503 + a "hint"
curl -N -X POST http://127.0.0.1:8141/v1/gaia/query \
  -H 'content-type: application/json' \
  -d '{"query":"What can you do?","run_id":"00000000-0000-4000-8000-000000000001","context":[],"can_answer_questions":false}'
```

A healthy run streams `status` / `token` events and ends with one `final`. If
`/v1/gaia/init` is 503, fix what its `hint` names and retry — the rest of your
integration is fine.

That `/query` call carries no `Authorization` header because `gaia serve` starts
the sidecar in dev mode. Against one started with a token, add
`-H "authorization: Bearer $TOKEN"` — a **401** here and a green `/health` is
that and nothing else (§6).

**A 503 from `/query` itself is a different condition**: every retained
session slot is busy and none is idle enough to evict (SPEC §5.2). Do NOT
loop on `/v1/gaia/init` — it will report ready. Wait for a running turn to
finish (or close an idle session) and retry the same `/query`.

**Three more refusals are yours to avoid**, each naming its fix in `detail`
(SPEC §5.2 has the reasoning):

- **409 — the `run_id` is still in flight.** You mint it, so mint a fresh UUID
  per request; reusing one would leave the earlier run with no way to be
  cancelled.
- **409 — `model` differs from what this `session_id` was built with.** Only
  construction reads a model, so it cannot be applied to the retained agent.
  Omit `model` to stay on the session's current one, or start a new
  `session_id` to switch.
- **400 — the `Host` header is absent or empty.** The loopback check fails
  closed, so omitting the header is refused rather than served. Send
  `Host: 127.0.0.1:<port>`; every real HTTP client already does.

For the full wire contract, lock schema, exit codes, and timeout table, see
[`SPEC.md`](./SPEC.md). For the user-facing overview, see [`README.md`](./README.md)
and <https://amd-gaia.ai/docs/guides/gaia>.
