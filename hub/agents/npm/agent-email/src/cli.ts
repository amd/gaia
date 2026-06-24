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
import os from "node:os";
import path from "node:path";

import { fetchBinary } from "./fetch.js";
import { shutdown, startSidecar } from "./lifecycle.js";
import { currentPlatformKey, loadLock } from "./platform.js";
import { AgentEmailError } from "./errors.js";

interface ParsedArgs {
  _: string[];
  flags: Record<string, string | boolean>;
}

// Flags that take a value (`--out <dir>`); everything else is a boolean switch.
// Being explicit avoids the footgun where `--base-url --force` silently swallows
// the next flag as a value (or drops the value).
const VALUE_FLAGS = new Set(["out", "base-url", "platform", "port"]);

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
  agent-email fetch --out <dir> [options]   Download + SHA-256 verify the binary
  agent-email version                       Print package + lock manifest info
  agent-email help                          Show this help

playground options:
  --port <n>          Bind port (default 8131)
  --out <dir>         Where to cache the binary (default: a temp dir)
  --base-url <url>    Override the download base URL from binaries.lock.json
  --no-open           Don't auto-open the browser; just print the URL

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
    spawn(cmd, args as string[], { stdio: "ignore", detached: true }).unref();
  } catch {
    /* non-fatal: the URL is printed regardless */
  }
}

async function cmdPlayground(args: ParsedArgs): Promise<number> {
  const port =
    typeof args.flags.port === "string" ? Number(args.flags.port) : 8131;
  if (!Number.isInteger(port) || port <= 0) {
    process.stderr.write(`error: --port must be a positive integer (got ${String(args.flags.port)})\n`);
    return 2;
  }
  // Cache the binary in a temp dir by default so a throwaway `npx ... playground`
  // run doesn't litter the cwd; fetchBinary is a cache-hit on the second run.
  const outDir =
    typeof args.flags.out === "string"
      ? args.flags.out
      : path.join(os.tmpdir(), "amd-gaia-agent-email");

  process.stdout.write(`[agent-email] fetching the sidecar binary -> ${outDir}\n`);
  const { binaryPath } = await fetchBinary({
    outDir,
    baseUrl: typeof args.flags["base-url"] === "string" ? args.flags["base-url"] : undefined,
  });

  process.stdout.write(`[agent-email] starting the sidecar on 127.0.0.1:${port} ...\n`);
  const sidecar = await startSidecar({ binaryPath, port });

  const url = `http://127.0.0.1:${port}/v1/email/playground`;
  process.stdout.write(`\n  ▸ Playground: ${url}\n`);
  process.stdout.write(`    (Lemonade must be running for live triage — the page tells you if it isn't.)\n`);
  process.stdout.write(`    Press Ctrl+C to stop the sidecar.\n\n`);
  if (!args.flags["no-open"]) openBrowser(url);

  // Stay alive until interrupted, then shut the sidecar down cleanly.
  await new Promise<void>((resolve) => {
    const stop = (): void => {
      process.stdout.write("\n[agent-email] stopping the sidecar ...\n");
      void shutdown(sidecar).finally(resolve);
    };
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
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
