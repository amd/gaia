#!/usr/bin/env node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * `gaia` — the one command that gets you running.
 *
 *   npx @amd-gaia/gaia                 fetch + verify both binaries, then launch the TUI
 *   npx @amd-gaia/gaia fetch           fetch + verify only
 *   npx @amd-gaia/gaia serve           run the agent sidecar alone (REST, no TUI)
 *   npx @amd-gaia/gaia version         print the lock manifest
 *
 * Every download is SHA-256 verified against `binaries.lock.json` before it is
 * written, and a mismatch or a placeholder hash aborts the run. There is no
 * "continue anyway" path.
 */

import { readdirSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { GaiaError } from "./errors.js";
import {
  daemonSidecarCacheDir,
  defaultCacheDir,
  fetchAll,
  fetchBinary,
} from "./fetch.js";
import {
  DEFAULT_PORT,
  RESERVED_PORT,
  runTui,
  shutdown,
  startSidecar,
} from "./lifecycle.js";
import {
  COMPONENTS,
  type ComponentName,
  componentBaseUrl,
  componentLock,
  currentPlatformKey,
  loadLock,
  platformsFor,
} from "./platform.js";

interface ParsedArgs {
  _: string[];
  flags: Record<string, string | boolean>;
  /** Everything after a bare `--`, forwarded verbatim to the TUI. */
  passthrough: string[];
}

// Flags that consume a value. Being explicit avoids the footgun where
// `--base-url --force` silently swallows the next flag as a value.
const VALUE_FLAGS = new Set([
  "base-url",
  "platform",
  "port",
  "cache-dir",
  "sidecar-dir",
  "component",
]);

/** Raised for a malformed command line; the caller turns it into exit 2. */
export class UsageError extends Error {}

export function parseArgs(argv: string[]): ParsedArgs {
  const out: ParsedArgs = { _: [], flags: {}, passthrough: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--") {
      out.passthrough = argv.slice(i + 1);
      break;
    }
    if (a.startsWith("--")) {
      // Both `--flag value` and `--flag=value` — dropping the second silently
      // would download from the default hub after the user pointed us elsewhere.
      const eq = a.indexOf("=");
      const key = eq === -1 ? a.slice(2) : a.slice(2, eq);
      const inline = eq === -1 ? undefined : a.slice(eq + 1);
      if (VALUE_FLAGS.has(key)) {
        if (inline !== undefined) {
          if (inline === "") throw new UsageError(`--${key} was given an empty value`);
          out.flags[key] = inline;
          continue;
        }
        const next = argv[i + 1];
        if (next === undefined || next.startsWith("--")) {
          throw new UsageError(`--${key} expects a value`);
        }
        out.flags[key] = next;
        i++;
      } else {
        if (inline !== undefined) {
          throw new UsageError(`--${key} does not take a value (got '${a}')`);
        }
        out.flags[key] = true;
      }
    } else if (a === "-h") {
      out.flags["help"] = true;
    } else {
      out._.push(a);
    }
  }
  return out;
}

const HELP = `@amd-gaia/gaia — run GAIA locally on AMD Ryzen AI

Usage:
  gaia [run] [options] [-- <tui args>]   Fetch + verify both binaries, then launch the TUI
  gaia fetch [options]                   Download + SHA-256 verify the binaries only
  gaia serve [options]                   Run the agent sidecar alone (REST API, no TUI)
  gaia version                           Print the lock manifest and this host's platform
  gaia help                              Show this help

run options:
  --base-url <url>     Override the per-component download base URL from
                       binaries.lock.json (applies to every component fetched).
                       Must be https unless --allow-insecure-base-url is given.
  --allow-insecure-base-url
                       Permit a non-https --base-url (a trusted local mirror)
  --cache-dir <dir>    Where to cache the TUI binary
  --sidecar-dir <dir>  Where to install the agent sidecar
                       (default ~/.gaia/agents/gaia — the daemon's own cache)
  --force              Re-download even when a verified binary is already cached
  -- <tui args>        Forward everything after \`--\` to gaia-tui verbatim

fetch options:
  --component <name>   Fetch only one of: ${COMPONENTS.join(", ")} (default: both)
  --platform <key>     Override the platform key (e.g. linux-x64). Default: this host
  plus every run option above.

serve options:
  --port <n>           Bind port (default ${DEFAULT_PORT}; ${RESERVED_PORT} is reserved and refused)
  --base-url, --sidecar-dir, --force as above. \`serve\` never downloads the TUI,
  so --cache-dir is refused rather than ignored.

Notes:
  * No binaries are committed to the repo. Every download is SHA-256 verified
    against binaries.lock.json and FAILS LOUDLY on any mismatch.
  * A placeholder hash in the lock blocks the fetch outright, so an unverifiable
    binary can never be trusted.
  * The agent sidecar has no arm64 Linux or arm64 Windows build; the TUI does.
    \`gaia version\` prints the exact per-component platform matrix.
  * The TUI is the published \`terminal-hub\` component — byte-identical to the
    \`gaia tui\` a core install runs, so its behaviour cannot drift from it.
  * Set DEBUG=gaia for download, spawn, and sidecar output on stderr.
`;

const flagStr = (args: ParsedArgs, k: string): string | undefined =>
  typeof args.flags[k] === "string" ? (args.flags[k] as string) : undefined;

interface DownloadOptions {
  baseUrl?: string;
  platformKey?: string;
  force: boolean;
  tuiDir?: string;
  sidecarDir?: string;
}

/**
 * Which commands actually READ each value flag, and why the others refuse it.
 * A flag a command ignores is worse than one it rejects: `run --port 9000`
 * used to parse fine and silently come up on the default port.
 */
const VALUE_FLAG_SCOPE: Record<string, { commands: readonly string[]; why: string }> = {
  "base-url": { commands: ["run", "fetch", "serve"], why: "it only affects downloads" },
  "sidecar-dir": { commands: ["run", "fetch", "serve"], why: "it only affects downloads" },
  platform: {
    commands: ["fetch"],
    why:
      "`run` and `serve` execute the binary, so it must be built for this host " +
      `(${currentPlatformKey()})`,
  },
  port: {
    commands: ["serve"],
    why: "only `serve` binds a port; `run` reaches the agent through the daemon, which picks its own",
  },
  component: {
    commands: ["fetch"],
    why: "`run` needs both binaries and `serve` needs the sidecar, so neither can fetch a subset",
  },
  "cache-dir": {
    commands: ["run", "fetch"],
    why: "it caches the TUI, which `serve` never downloads (use --sidecar-dir)",
  },
};

/** Refuse a value flag the chosen command would otherwise silently ignore. */
function assertFlagsInScope(args: ParsedArgs, cmd: string): void {
  for (const [flag, scope] of Object.entries(VALUE_FLAG_SCOPE)) {
    if (args.flags[flag] === undefined || scope.commands.includes(cmd)) continue;
    throw new UsageError(
      `--${flag} is not valid for \`gaia ${cmd}\` — ${scope.why}. ` +
        `It is accepted by: ${scope.commands.map((c) => `\`gaia ${c}\``).join(", ")}.`,
    );
  }
}

/**
 * Require https for a download source. Integrity still holds over plaintext
 * (the SHA is pinned), but a MITM then surfaces as a baffling IntegrityError
 * instead of the transport failure it is.
 */
function checkedBaseUrl(args: ParsedArgs): string | undefined {
  const raw = flagStr(args, "base-url");
  if (raw === undefined) return undefined;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new UsageError(
      `--base-url '${raw}' is not a valid absolute URL (expected e.g. https://host/path).`,
    );
  }
  if (parsed.protocol !== "https:" && !args.flags["allow-insecure-base-url"]) {
    throw new UsageError(
      `--base-url '${raw}' uses ${parsed.protocol}, not https. Downloads are ` +
        "SHA-256 verified either way, but plaintext turns tampering into a " +
        "confusing integrity failure instead of a transport error. Use an https " +
        "URL, or pass --allow-insecure-base-url for a trusted local mirror.",
    );
  }
  return raw;
}

/** Shared download options. Scope and scheme are validated before we get here. */
function downloadOptions(args: ParsedArgs, allowPlatform = false): DownloadOptions {
  const platformKey = flagStr(args, "platform");
  return {
    baseUrl: checkedBaseUrl(args),
    ...(allowPlatform && platformKey !== undefined ? { platformKey } : {}),
    force: Boolean(args.flags["force"]),
    tuiDir: flagStr(args, "cache-dir"),
    sidecarDir: flagStr(args, "sidecar-dir"),
  };
}

/**
 * The child's PATH with this package's own bin directory removed.
 *
 * npm installs our shim as `gaia`, and the TUI starts the daemon by resolving
 * `gaia` on PATH (tui/internal/daemon/client.go) expecting the *Python* CLI.
 * Left alone, the TUI would re-invoke us with `daemon start` and the daemon would
 * never come up. Returns undefined when PATH is unset.
 */
export function pathWithoutOwnShim(
  rawPath: string | undefined = process.env["PATH"],
  argv1: string | undefined = process.argv[1],
): string | undefined {
  if (rawPath === undefined || !argv1) return rawPath;
  const sep = process.platform === "win32" ? ";" : ":";
  let ownBinDir: string;
  let ownName: string;
  try {
    ownBinDir = path.resolve(path.dirname(argv1));
    ownName = path.basename(argv1);
  } catch {
    return rawPath;
  }
  const same = (dir: string): boolean => {
    const resolved = path.resolve(dir);
    return process.platform === "win32"
      ? resolved.toLowerCase() === ownBinDir.toLowerCase()
      : resolved === ownBinDir;
  };
  const entries = rawPath.split(sep).filter((d) => d !== "");
  const kept = entries.filter((d) => !same(d));
  if (kept.length === entries.length) return kept.join(sep);
  // A dir holding nothing but our own shim (an npx temp dir, node_modules/.bin)
  // costs nothing to drop. A SHARED bin dir must NOT be dropped — that is where
  // python3 / lemonade-server / the real `gaia` live — so it moves to the end
  // instead, letting any other `gaia` on PATH win while its siblings survive.
  return (isExclusivelyOurs(ownBinDir, ownName) ? kept : [...kept, ownBinDir]).join(sep);
}

/** True when every file in `dir` is a shim for `name` (`gaia`, `gaia.cmd`, …). */
function isExclusivelyOurs(dir: string, name: string): boolean {
  const stem = name.replace(/\.(cmd|bat|ps1|exe)$/i, "");
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return false; // unreadable → treat as shared, the non-destructive branch
  }
  return (
    entries.length > 0 &&
    entries.every((e) => e.replace(/\.(cmd|bat|ps1|exe)$/i, "") === stem)
  );
}

/** Validate `--port`. Rejects out-of-range values and the reserved port. */
export function resolvePort(raw: string | boolean | undefined): { port: number } | { error: string } {
  // Strict digits, not Number(): that accepts "0x2710", " 80 ", and "1e3",
  // so a typo would quietly bind a port the user never named.
  const port = typeof raw === "string" ? (/^\d+$/.test(raw) ? Number(raw) : NaN) : DEFAULT_PORT;
  if (!Number.isInteger(port) || port <= 0 || port > 65535 || port === RESERVED_PORT) {
    return {
      error: `--port must be a port in 1..65535 and not ${RESERVED_PORT} (got ${String(raw)})`,
    };
  }
  return { port };
}

async function cmdRun(args: ParsedArgs): Promise<number> {
  const opts = downloadOptions(args);
  process.stderr.write("[gaia] fetching and verifying the agent sidecar and the terminal UI ...\n");
  const { sidecar, tui } = await fetchAll(opts);
  process.stderr.write(
    `[gaia] sidecar ${sidecar.cached ? "cached" : "installed"} -> ${sidecar.binaryPath}\n` +
      `[gaia] tui     ${tui.cached ? "cached" : "installed"} -> ${tui.binaryPath}\n` +
      "[gaia] starting the terminal UI ...\n",
  );
  // The TUI owns the daemon handshake and the daemon owns the sidecar process —
  // the TUI never holds a sidecar bearer token, so starting one here would only
  // fight it for the port. Our job is to put a *verified* binary where the daemon
  // looks, which fetchAll just did.
  const childPath = pathWithoutOwnShim();
  return runTui({
    binaryPath: tui.binaryPath,
    args: args.passthrough,
    env: childPath === undefined ? {} : { PATH: childPath, Path: childPath },
  });
}

async function cmdFetch(args: ParsedArgs): Promise<number> {
  const opts = downloadOptions(args, true);
  const only = args.flags["component"];
  const results = [];
  if (typeof only === "string") {
    if (!(COMPONENTS as readonly string[]).includes(only)) {
      throw new UsageError(
        `unknown --component '${only}'; expected one of ${COMPONENTS.join(", ")}`,
      );
    }
    const component = only as ComponentName;
    const lock = loadLock();
    results.push(
      await fetchBinary({
        component,
        lock,
        outDir:
          component === "sidecar"
            ? (opts.sidecarDir ?? daemonSidecarCacheDir())
            : (opts.tuiDir ?? defaultCacheDir(lock.agentVersion)),
        baseUrl: opts.baseUrl,
        platformKey: opts.platformKey,
        force: opts.force,
      }),
    );
  } else {
    const all = await fetchAll(opts);
    results.push(all.sidecar, all.tui);
  }
  process.stdout.write(
    JSON.stringify(
      {
        ok: true,
        binaries: results.map((r) => ({
          component: r.component,
          binaryPath: r.binaryPath,
          platform: r.platformKey,
          sha256: r.sha256,
          cached: r.cached,
          url: r.url,
        })),
      },
      null,
      2,
    ) + "\n",
  );
  return 0;
}

const SERVE_SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"] as const;

async function cmdServe(args: ParsedArgs): Promise<number> {
  const parsed = resolvePort(args.flags["port"]);
  if ("error" in parsed) {
    process.stderr.write(`error: ${parsed.error}\n`);
    return 2;
  }
  const { port } = parsed;
  const opts = downloadOptions(args);
  process.stderr.write("[gaia] fetching and verifying the agent sidecar ...\n");
  const result = await fetchBinary({
    component: "sidecar",
    outDir: opts.sidecarDir ?? daemonSidecarCacheDir(),
    baseUrl: opts.baseUrl,
    force: opts.force,
  });

  // We own the lifecycle here (autoCleanup off) so the graceful shutdown below
  // actually runs; the default auto-reaper would SIGKILL the tree first.
  const sidecar = await startSidecar({
    binaryPath: result.binaryPath,
    port,
    autoCleanup: false,
  });
  try {
    process.stdout.write(`\n  ▸ GAIA agent: ${sidecar.baseUrl}/v1/gaia/query\n`);
    process.stdout.write(`    Health:     ${sidecar.baseUrl}/health\n`);
    process.stdout.write("    Lemonade must be running for live queries. Ctrl+C to stop.\n\n");
    let stop!: () => void;
    const stopped = new Promise<void>((resolve) => {
      let stopping = false;
      stop = (): void => {
        if (stopping) {
          // Absorbed, so say what is happening — otherwise a repeat Ctrl+C
          // during a slow teardown just looks like a frozen terminal.
          process.stderr.write(
            `[gaia] already stopping the sidecar (pid ${String(sidecar.child.pid)}); ` +
              "waiting for its process tree to exit ...\n",
          );
          return;
        }
        stopping = true;
        process.stderr.write("\n[gaia] stopping the sidecar ...\n");
        void shutdown(sidecar).catch(() => undefined).finally(resolve);
      };
      // process.on, not once: a second Ctrl+C during the shutdown would
      // otherwise hit Node's default disposition and kill us mid-teardown,
      // orphaning the detached sidecar tree on port 8141. The `stopping` guard
      // absorbs the repeats.
      for (const sig of SERVE_SIGNALS) process.on(sig, stop);
    });
    try {
      await stopped;
    } finally {
      // Left installed, they would swallow every later signal AND suppress
      // Node's default disposition, so Ctrl+C would stop working entirely.
      for (const sig of SERVE_SIGNALS) process.removeListener(sig, stop);
    }
    return 0;
  } catch (e) {
    await shutdown(sidecar).catch(() => undefined);
    throw e;
  }
}

function cmdVersion(): number {
  const lock = loadLock();
  process.stdout.write(
    JSON.stringify(
      {
        agentVersion: lock.agentVersion,
        schemaVersion: lock.schemaVersion,
        currentPlatform: currentPlatformKey(),
        // Per component: each ships from its own hub lane at its own version.
        components: Object.fromEntries(
          COMPONENTS.map((c) => [
            c,
            {
              componentVersion: componentLock(lock, c).componentVersion,
              baseUrl: componentBaseUrl(lock, c),
              platforms: platformsFor(lock, c),
            },
          ]),
        ),
      },
      null,
      2,
    ) + "\n",
  );
  return 0;
}

const KNOWN_COMMANDS = ["run", "fetch", "serve", "version", "help"] as const;

export async function main(argv: string[]): Promise<number> {
  const args = parseArgs(argv);
  const explicit = args._[0];
  const cmd = explicit ?? (args.flags["help"] ? "help" : "run");
  if (args.flags["help"] && cmd !== "help") {
    process.stdout.write(HELP);
    return 0;
  }
  if (!(KNOWN_COMMANDS as readonly string[]).includes(cmd)) {
    throw new UsageError(`unknown command '${cmd}'`);
  }
  // A dropped positional means a mistyped invocation whose flags may also be
  // wrong; refuse rather than run something the user did not ask for.
  const extra = explicit === undefined ? args._ : args._.slice(1);
  if (extra.length > 0) {
    throw new UsageError(
      `unexpected argument${extra.length > 1 ? "s" : ""} ${extra.map((a) => `'${a}'`).join(", ")}. ` +
        "Arguments for the terminal UI go after a bare `--`.",
    );
  }
  assertFlagsInScope(args, cmd);
  switch (cmd) {
    case "run":
      return cmdRun(args);
    case "fetch":
      return cmdFetch(args);
    case "serve":
      return cmdServe(args);
    case "version":
      return cmdVersion();
    default:
      process.stdout.write(HELP);
      return 0;
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
  main(process.argv.slice(2))
    // exitCode rather than process.exit(): the latter can truncate the JSON
    // `fetch`/`version` just wrote into an async stdout pipe.
    .then((code) => {
      process.exitCode = code;
    })
    .catch((e) => {
      // Fail loudly with an actionable message; never swallow.
      if (e instanceof UsageError) {
        process.stderr.write(`error: ${e.message}\n\n${HELP}`);
        process.exitCode = 2;
      } else if (e instanceof GaiaError) {
        process.stderr.write(`[gaia] ${e.name}: ${e.message}\n`);
        process.exitCode = 1;
      } else {
        process.stderr.write(`[gaia] unexpected error: ${(e as Error).stack ?? e}\n`);
        process.exitCode = 1;
      }
    });
}
