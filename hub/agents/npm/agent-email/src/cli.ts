#!/usr/bin/env node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `agent-email` CLI. Build-time entry point exposed via the package `bin`:
 *
 *   npx @amd-gaia/agent-email fetch --out <dir> [--base-url <url>] [--platform <key>] [--force]
 *   npx @amd-gaia/agent-email version
 *   npx @amd-gaia/agent-email help
 *
 * `fetch` is the supported path (download + SHA-256 verify a binary into a
 * resources dir at build time). A `--runtime` note is accepted as an opt-in
 * marker but build-time fetch remains the recommended flow.
 */

import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fetchBinary } from "./fetch.js";
import { connectSidecar, shutdown, startSidecar } from "./lifecycle.js";
import { currentPlatformKey, loadLock } from "./platform.js";
import { AgentEmailError } from "./errors.js";

interface ParsedArgs {
  _: string[];
  flags: Record<string, string | boolean>;
}

// Flags that take a value (`--out <dir>`); everything else is a boolean switch.
// Being explicit avoids the footgun where `--base-url --force` silently swallows
// the next flag as a value (or drops the value).
const VALUE_FLAGS = new Set(["out", "base-url", "platform", "port", "python", "cmd"]);

function parseArgs(argv: string[]): ParsedArgs {
  const out: ParsedArgs = { _: [], flags: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith("--")) {
      const key = a.slice(2);
      if (VALUE_FLAGS.has(key)) {
        const next = argv[i + 1];
        if (next === undefined || next.startsWith("--")) {
          process.stderr.write(`warning: --${key} expects a value; ignoring\n`);
          continue;
        }
        out.flags[key] = next;
        i++;
      } else {
        out.flags[key] = true;
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

const HELP = `@amd-gaia/agent-email — GAIA email agent binary fetcher + client

Usage:
  agent-email playground [options]          Fetch + run the sidecar and open the playground
  agent-email dev [options]                 Run the SOURCE agent with auto-reload (fast dev loop)
  agent-email fetch --out <dir> [options]   Download + SHA-256 verify the binary
  agent-email version                       Print package + lock manifest info
  agent-email help                          Show this help

playground options:
  --port <n>          Bind port (default 8131)
  --out <dir>         Where to cache the binary (default: a temp dir)
  --base-url <url>    Override the download base URL from binaries.lock.json
  --no-open           Don't auto-open the browser; just print the URL

dev options (runs the Python source, NOT the frozen binary — for iterating on the agent):
  --port <n>          Bind port (default 8131)
  --python <path>     Python interpreter to run \`-m gaia_agent_email.server\` with
                      (use your venv's python where the package is \`pip install -e\`'d)
  --cmd <path>        Override the launcher executable (default: \`gaia-agent-email\`)
  Requires \`pip install -e hub/agents/python/email\` (or the wheel) so the
  \`gaia-agent-email\` console script / module is importable. Edit the Python and
  it auto-reloads; attach your app with \`connectSidecar({ baseUrl })\`.

fetch options:
  --out <dir>         Resources dir to write the verified binary into (required)
  --base-url <url>    Override the download base URL from binaries.lock.json
  --platform <key>    Override platform key (e.g. linux-x64). Default: this host
  --force             Re-download even if a verified binary already exists
  --runtime           Opt-in marker for runtime fetch (build-time is supported)

Notes:
  * No binaries are committed. fetch verifies SHA-256 against binaries.lock.json
    and FAILS LOUDLY on any mismatch.
  * If the lock has a placeholder hash for a platform, fetch is blocked for it so
    a bad binary can never be trusted. Build the binary locally
    (hub/agents/python/email/packaging/freeze.py) and point the lifecycle helpers
    at it directly.
`;

async function cmdFetch(args: ParsedArgs): Promise<number> {
  const out = args.flags.out;
  if (typeof out !== "string") {
    process.stderr.write("error: --out <dir> is required for fetch\n\n" + HELP);
    return 2;
  }
  if (args.flags.runtime) {
    process.stderr.write(
      "[agent-email] --runtime set: build-time fetch is the supported path; proceeding.\n",
    );
  }
  const result = await fetchBinary({
    outDir: out,
    baseUrl: typeof args.flags["base-url"] === "string" ? args.flags["base-url"] : undefined,
    platformKey:
      typeof args.flags.platform === "string" ? args.flags.platform : undefined,
    force: Boolean(args.flags.force),
  });
  process.stdout.write(
    JSON.stringify(
      {
        ok: true,
        binaryPath: result.binaryPath,
        platform: result.platformKey,
        sha256: result.sha256,
        cached: result.cached,
        url: result.url,
      },
      null,
      2,
    ) + "\n",
  );
  return 0;
}

/** Best-effort cross-platform "open this URL in the default browser". */
function openBrowser(url: string): void {
  try {
    const [cmd, args] =
      process.platform === "darwin"
        ? ["open", [url]]
        : process.platform === "win32"
          ? ["cmd", ["/c", "start", "", url]]
          : ["xdg-open", [url]];
    const child = spawn(cmd, args as string[], { stdio: "ignore", detached: true });
    // A missing opener (headless / container / WSL without wslu) is reported via
    // an async 'error' event, NOT a sync throw — swallow it here, or Node re-throws
    // it as an uncaughtException and the sidecar auto-reaper tears everything down.
    child.on("error", () => {
      /* non-fatal: the URL was already printed above */
    });
    child.unref();
  } catch {
    /* non-fatal: the URL is printed regardless */
  }
}

/**
 * Resolve + validate the `--port` flag. Returns the port, or an actionable error
 * string for the friendly `exit 2` path. Rejects out-of-range ports and 4001
 * (which `spawnSidecar` reserves and would otherwise surface as a generic crash).
 */
export function resolvePlaygroundPort(
  raw: string | boolean | undefined,
): { port: number } | { error: string } {
  const port = typeof raw === "string" ? Number(raw) : 8131;
  if (!Number.isInteger(port) || port <= 0 || port > 65535 || port === 4001) {
    return {
      error: `--port must be a port in 1..65535 and not 4001 (got ${String(raw)})`,
    };
  }
  return { port };
}

/** Default cache dir for the fetched binary (keeps a throwaway run out of cwd). */
export const DEFAULT_PLAYGROUND_CACHE = path.join(os.tmpdir(), "amd-gaia-agent-email");

async function cmdPlayground(args: ParsedArgs): Promise<number> {
  const parsed = resolvePlaygroundPort(args.flags.port);
  if ("error" in parsed) {
    process.stderr.write(`error: ${parsed.error}\n`);
    return 2;
  }
  const { port } = parsed;
  // Cache the binary in a temp dir by default so a throwaway `npx ... playground`
  // run doesn't litter the cwd; fetchBinary is a cache-hit on the second run.
  const outDir =
    typeof args.flags.out === "string" ? args.flags.out : DEFAULT_PLAYGROUND_CACHE;

  process.stdout.write(`[agent-email] fetching the sidecar binary -> ${outDir}\n`);
  const { binaryPath } = await fetchBinary({
    outDir,
    baseUrl: typeof args.flags["base-url"] === "string" ? args.flags["base-url"] : undefined,
  });

  process.stdout.write(`[agent-email] starting the sidecar on 127.0.0.1:${port} ...\n`);
  // Own the lifecycle here (autoCleanup off) so the graceful shutdown below
  // actually runs — the default auto-reaper would SIGKILL the tree first.
  const sidecar = await startSidecar({ binaryPath, port, autoCleanup: false });

  // autoCleanup is off, so nothing reaps the sidecar on a throw until the signal
  // handlers below are installed. A throw in that window (e.g. EPIPE from a stdout
  // write into a closed pipe — `npx … playground | head`) would orphan it, so guard
  // the whole post-start region and shut down on the way out.
  try {
    const url = `http://127.0.0.1:${port}/v1/email/playground`;
    process.stdout.write(`\n  ▸ Playground: ${url}\n`);
    process.stdout.write(`    (Lemonade must be running for live triage — the page tells you if it isn't.)\n`);
    process.stdout.write(`    Press Ctrl+C to stop the sidecar.\n\n`);
    if (!args.flags["no-open"]) openBrowser(url);

    // Stay alive until interrupted, then shut the sidecar down cleanly. We own all
    // the signals the auto-reaper would have handled (it's off, above).
    await new Promise<void>((resolve) => {
      let stopping = false;
      const stop = (): void => {
        if (stopping) return; // a second signal shouldn't re-enter shutdown
        stopping = true;
        process.stdout.write("\n[agent-email] stopping the sidecar ...\n");
        void shutdown(sidecar)
          .catch(() => undefined)
          .finally(resolve);
      };
      for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"] as const) {
        process.once(sig, stop);
      }
    });
    return 0;
  } catch (e) {
    await shutdown(sidecar).catch(() => undefined);
    throw e;
  }
}

/** SIGKILL the launched dev-server process tree (uvicorn --reload forks a child). */
function killDevTree(child: ReturnType<typeof spawn>): void {
  if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  try {
    if (process.platform === "win32") {
      spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-child.pid, "SIGTERM");
    }
  } catch {
    /* already gone */
  }
}

/**
 * Resolve the launcher command for `dev`. Prefers `--python -m
 * gaia_agent_email.server` when a Python path is given (venv-friendly), else the
 * `gaia-agent-email` console script (or a `--cmd` override), always with the
 * `serve --reload` args.
 */
export function resolveDevCommand(
  flags: Record<string, string | boolean>,
  host: string,
  port: number,
): { cmd: string; args: string[] } {
  const serveArgs = ["serve", "--reload", "--host", host, "--port", String(port)];
  if (typeof flags.python === "string") {
    return { cmd: flags.python, args: ["-m", "gaia_agent_email.server", ...serveArgs] };
  }
  const cmd = typeof flags.cmd === "string" ? flags.cmd : "gaia-agent-email";
  return { cmd, args: serveArgs };
}

async function cmdDev(args: ParsedArgs): Promise<number> {
  const parsed = resolvePlaygroundPort(args.flags.port); // same validation (rejects 4001)
  if ("error" in parsed) {
    process.stderr.write(`error: ${parsed.error}\n`);
    return 2;
  }
  const { port } = parsed;
  const host = "127.0.0.1";
  const { cmd, args: cmdArgs } = resolveDevCommand(args.flags, host, port);

  process.stdout.write(
    `[agent-email] starting the SOURCE agent (auto-reload): ${cmd} ${cmdArgs.join(" ")}\n`,
  );
  // Inherit stdio so uvicorn's reload logs show live. Detached on POSIX so we can
  // tree-kill the reloader's child on the way out.
  const child = spawn(cmd, cmdArgs, {
    stdio: "inherit",
    detached: process.platform !== "win32",
  });

  let launchError: Error | undefined;
  child.on("error", (e) => {
    launchError = e;
    process.stderr.write(
      `[agent-email] failed to launch '${cmd}': ${e.message}\n` +
        "  Is the Python package installed? `pip install -e hub/agents/python/email`\n" +
        "  Or point at your venv: `agent-email dev --python /path/to/venv/bin/python`\n",
    );
  });

  const baseUrl = `http://${host}:${port}`;
  try {
    // Reload startup re-imports the app graph, so allow generous headroom.
    const dev = await connectSidecar({ baseUrl, healthTimeoutMs: 60_000 });
    process.stdout.write(`\n  ▸ Email agent (source): ${dev.baseUrl}\n`);
    process.stdout.write(`    Playground: ${dev.baseUrl}/v1/email/playground\n`);
    process.stdout.write(
      `    Attach your app with: connectSidecar({ baseUrl: "${dev.baseUrl}" })\n`,
    );
    process.stdout.write(
      "    Edit the Python under gaia_agent_email/ and it auto-reloads. Ctrl+C to stop.\n\n",
    );
  } catch (e) {
    killDevTree(child);
    if (launchError) return 1; // the spawn error already printed an actionable hint
    throw e;
  }

  await new Promise<void>((resolve) => {
    let stopping = false;
    const stop = (): void => {
      if (stopping) return;
      stopping = true;
      process.stdout.write("\n[agent-email] stopping the dev server ...\n");
      killDevTree(child);
      resolve();
    };
    child.once("exit", stop);
    for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"] as const) {
      process.once(sig, stop);
    }
  });
  return 0;
}

function cmdVersion(): number {
  const lock = loadLock();
  process.stdout.write(
    JSON.stringify(
      {
        agentVersion: lock.agentVersion,
        schemaVersion: lock.schemaVersion,
        currentPlatform: currentPlatformKey(),
        platforms: Object.keys(lock.binaries),
        baseUrl: lock.baseUrl,
      },
      null,
      2,
    ) + "\n",
  );
  return 0;
}

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0] ?? "help";
  switch (cmd) {
    case "playground":
      return cmdPlayground(args);
    case "dev":
      return cmdDev(args);
    case "fetch":
      return cmdFetch(args);
    case "version":
      return cmdVersion();
    case "help":
    case undefined:
      process.stdout.write(HELP);
      return 0;
    default:
      process.stderr.write(`error: unknown command '${cmd}'\n\n${HELP}`);
      return 2;
  }
}

/** True when this file is the entry point (so importing it for tests is a no-op). */
function invokedDirectly(): boolean {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}

if (invokedDirectly()) {
  main()
    .then((code) => process.exit(code))
    .catch((e) => {
      // Fail loudly with an actionable message; never swallow.
      if (e instanceof AgentEmailError) {
        process.stderr.write(`[agent-email] ${e.name}: ${e.message}\n`);
      } else {
        process.stderr.write(`[agent-email] unexpected error: ${(e as Error).stack ?? e}\n`);
      }
      process.exit(1);
    });
}
