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

import { fetchBinary } from "./fetch.js";
import { currentPlatformKey, loadLock } from "./platform.js";
import { AgentEmailError } from "./errors.js";

interface ParsedArgs {
  _: string[];
  flags: Record<string, string | boolean>;
}

// Flags that take a value (`--out <dir>`); everything else is a boolean switch.
// Being explicit avoids the footgun where `--base-url --force` silently swallows
// the next flag as a value (or drops the value).
const VALUE_FLAGS = new Set(["out", "base-url", "platform"]);

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
  agent-email fetch --out <dir> [options]   Download + SHA-256 verify the binary
  agent-email version                       Print package + lock manifest info
  agent-email help                          Show this help

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
