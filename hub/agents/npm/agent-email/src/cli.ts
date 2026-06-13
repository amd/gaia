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

function parseArgs(argv: string[]): ParsedArgs {
  const out: ParsedArgs = { _: [], flags: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("--")) {
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
  --base-url <url>    Override the download base URL (real R2 URL pending #1648)
  --platform <key>    Override platform key (e.g. linux-x64). Default: this host
  --force             Re-download even if a verified binary already exists
  --runtime           Opt-in marker for runtime fetch (build-time is supported)

Notes:
  * No binaries are committed. fetch verifies SHA-256 against binaries.lock.json
    and FAILS LOUDLY on any mismatch.
  * Until R2 is wired (#1648) the lock ships placeholder hashes, so fetch is
    intentionally blocked. Pass --base-url and a lock with real hashes, or build
    the binary locally (hub/agents/python/email/packaging/freeze.py).
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
