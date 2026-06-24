// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Sidecar lifecycle: locate the frozen binary, spawn it, wait for readiness,
 * check the contract version, and shut it down cleanly (killing the whole
 * process tree).
 *
 * Tree-kill matters: a PyInstaller one-file build spawns a child uvicorn process
 * that `child.kill()` on the parent does NOT reap — leaving the port held. We
 * always kill the tree (`taskkill /F /T` on Windows; a detached process-group
 * kill on POSIX).
 */

import { type ChildProcess, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { EmailClient } from "./client.js";
import {
  BinaryNotFoundError,
  HealthTimeoutError,
  VersionMismatchError,
} from "./errors.js";
import { createLogger } from "./logger.js";
import { currentPlatformKey } from "./platform.js";
import { SCHEMA_VERSION, type VersionResponse } from "./types.js";

const log = createLogger("lifecycle");

const DEFAULT_HOST = "127.0.0.1";
// Matches server.py DEFAULT_PORT. NEVER 4001 (reserved).
const DEFAULT_PORT = 8131;

/** The executable basename the fetcher writes (platform-specific extension). */
export function executableName(platform: NodeJS.Platform = process.platform): string {
  return platform === "win32" ? "email-agent.exe" : "email-agent";
}

export interface ResolveOptions {
  /** Directory the binary was fetched into. */
  resourcesDir: string;
  /** Override the executable basename (defaults per-platform). */
  executable?: string;
}

/**
 * Resolve the path to the email-agent binary inside a resources dir. Fails
 * loudly if it is not present (no "maybe it's on PATH" guessing).
 */
export function resolveBinaryPath(opts: ResolveOptions): string {
  if (!opts?.resourcesDir) {
    throw new TypeError("resolveBinaryPath requires { resourcesDir }");
  }
  const exe = opts.executable ?? executableName();
  const full = path.resolve(opts.resourcesDir, exe);
  if (!fs.existsSync(full)) {
    throw new BinaryNotFoundError(
      `email-agent binary not found at ${full} (platform ${currentPlatformKey()}). ` +
        "Run the fetch step first: `npx @amd-gaia/agent-email fetch --out <resourcesDir>` " +
        "(or build it locally with hub/agents/python/email/packaging/freeze.py and copy it here).",
    );
  }
  return full;
}

export interface SpawnOptions {
  /** Absolute path to the binary. */
  binaryPath: string;
  /** Bind host. Default 127.0.0.1. */
  host?: string;
  /** Bind port. Default 8131. NEVER use 4001. */
  port?: number;
  /** Extra CLI args appended verbatim. */
  extraArgs?: string[];
  /** Extra env vars merged over process.env. */
  env?: NodeJS.ProcessEnv;
}

/** A running sidecar handle. */
export interface Sidecar {
  child: ChildProcess;
  host: string;
  port: number;
  baseUrl: string;
  /** A client bound to this sidecar's baseUrl. */
  client: EmailClient;
}

/**
 * Spawn the frozen sidecar. Does NOT wait for readiness — call
 * `waitForHealth` (or use `startSidecar` which does both).
 */
export function spawnSidecar(opts: SpawnOptions): Sidecar {
  if (!opts?.binaryPath) {
    throw new TypeError("spawnSidecar requires { binaryPath }");
  }
  if (!fs.existsSync(opts.binaryPath)) {
    throw new BinaryNotFoundError(`binary does not exist: ${opts.binaryPath}`);
  }
  const host = opts.host ?? DEFAULT_HOST;
  const port = opts.port ?? DEFAULT_PORT;
  if (port === 4001) {
    throw new RangeError("port 4001 is reserved and must never be used");
  }
  const args = ["--host", host, "--port", String(port)];
  if (opts.extraArgs?.length) args.push(...opts.extraArgs);

  log.info(`spawning ${opts.binaryPath} ${args.join(" ")}`);

  const child = spawn(opts.binaryPath, args, {
    // detached on POSIX → the child becomes a process-group leader so we can
    // signal the whole tree on shutdown. On Windows detached has different
    // semantics; we tree-kill via taskkill instead.
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, ...(opts.env ?? {}) },
  });

  child.stdout?.on("data", (d) => log.debug(`[sidecar stdout] ${String(d).trimEnd()}`));
  child.stderr?.on("data", (d) => log.debug(`[sidecar stderr] ${String(d).trimEnd()}`));
  child.on("exit", (code, signal) =>
    log.debug(`sidecar exited code=${code} signal=${signal}`),
  );
  child.on("error", (e) => log.error(`sidecar process error: ${e.message}`));

  const baseUrl = `http://${host}:${port}`;
  const client = new EmailClient({ baseUrl });
  return { child, host, port, baseUrl, client };
}

export interface WaitForHealthOptions {
  /** Total time to wait before failing loudly. Default 30000ms. */
  timeoutMs?: number;
  /** Poll interval. Default 250ms. */
  intervalMs?: number;
  /** A client to probe with (defaults to a new one bound to baseUrl). */
  client?: EmailClient;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Poll GET /health until the sidecar reports ok, or throw `HealthTimeoutError`.
 * Never silently assumes ready.
 */
export async function waitForHealth(
  baseUrl: string,
  opts: WaitForHealthOptions = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? 30_000;
  const intervalMs = opts.intervalMs ?? 250;
  const client = opts.client ?? new EmailClient({ baseUrl, timeoutMs: intervalMs * 4 });
  const deadline = Date.now() + timeoutMs;
  let lastErr = "";
  let attempts = 0;
  while (Date.now() < deadline) {
    attempts++;
    try {
      const h = await client.health();
      if (h.status === "ok") {
        log.info(`sidecar healthy after ${attempts} probe(s)`);
        return;
      }
      lastErr = `unexpected health status: ${JSON.stringify(h)}`;
    } catch (e) {
      lastErr = (e as Error).message;
    }
    await sleep(intervalMs);
  }
  throw new HealthTimeoutError(
    `sidecar at ${baseUrl} did not become healthy within ${timeoutMs}ms ` +
      `(${attempts} probes). Last error: ${lastErr}. ` +
      "Check the binary launched (enable DEBUG=agent-email for spawn logs) and that the port is free.",
  );
}

/** Parse "1.0" → 1 (major). Throws on a non-numeric major. */
function majorOf(version: string): number {
  const major = Number.parseInt(String(version).split(".")[0] ?? "", 10);
  if (Number.isNaN(major)) {
    throw new VersionMismatchError(`cannot parse apiVersion major from '${version}'`);
  }
  return major;
}

export interface VersionCheckOptions {
  /** The apiVersion the client was built against. Default SCHEMA_VERSION ("2.0"). */
  expectedApiVersion?: string;
}

/**
 * Fetch /version and refuse a sidecar whose apiVersion MAJOR differs from what
 * this client expects. A MAJOR bump means a breaking contract change, so we fail
 * loudly rather than send requests the server may reject or mis-handle. A higher
 * MINOR (same major) is accepted (backward-compatible additions).
 */
export async function checkVersion(
  client: EmailClient,
  opts: VersionCheckOptions = {},
): Promise<VersionResponse> {
  const expected = opts.expectedApiVersion ?? SCHEMA_VERSION;
  const info = await client.version();
  const expectedMajor = majorOf(expected);
  const actualMajor = majorOf(info.apiVersion);
  if (actualMajor !== expectedMajor) {
    throw new VersionMismatchError(
      `incompatible email-agent apiVersion: sidecar reports '${info.apiVersion}' ` +
        `(major ${actualMajor}) but this client expects major ${expectedMajor} ` +
        `('${expected}'). A major bump is a breaking contract change. ` +
        "Upgrade @amd-gaia/agent-email to a version matching the sidecar, or pin the sidecar binary.",
    );
  }
  log.info(`version OK: apiVersion=${info.apiVersion} agentVersion=${info.agentVersion}`);
  return info;
}

/**
 * Shut down the sidecar, killing the whole process tree (packaging/README.md, gotcha 6).
 * Resolves once the process has exited (or immediately if already dead).
 */
export async function shutdown(sidecar: Sidecar, timeoutMs = 5000): Promise<void> {
  const { child } = sidecar;
  if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) {
    log.debug("shutdown: sidecar already exited");
    return;
  }
  const pid = child.pid;
  log.info(`shutting down sidecar pid=${pid} (tree-kill)`);

  const exited = new Promise<void>((resolve) => {
    child.once("exit", () => resolve());
  });

  if (process.platform === "win32") {
    // Kill the whole tree — one-file PyInstaller orphans its uvicorn child.
    const { spawn: spawnKill } = await import("node:child_process");
    const killer = spawnKill("taskkill", ["/PID", String(pid), "/T", "/F"], {
      stdio: "ignore",
    });
    killer.on("error", (e) => log.error(`taskkill failed: ${e.message}`));
  } else {
    // Negative pid → signal the whole process group (we spawned detached).
    try {
      process.kill(-pid, "SIGTERM");
    } catch (e) {
      log.debug(`SIGTERM to group failed (${(e as Error).message}); trying direct`);
      try {
        child.kill("SIGTERM");
      } catch {
        /* already gone */
      }
    }
  }

  const timer = new Promise<"timeout">((resolve) =>
    setTimeout(() => resolve("timeout"), timeoutMs),
  );
  const result = await Promise.race([exited.then(() => "exited" as const), timer]);
  if (result === "timeout") {
    log.warn(`sidecar did not exit within ${timeoutMs}ms; sending SIGKILL/forced`);
    if (process.platform !== "win32") {
      try {
        process.kill(-pid, "SIGKILL");
      } catch {
        /* gone */
      }
    }
    await exited;
  }
  log.info("sidecar shut down");
}

export interface StartOptions extends SpawnOptions {
  /** Health-wait timeout. Default 30000ms. */
  healthTimeoutMs?: number;
  /** Verify the contract apiVersion after health (default true). */
  verifyVersion?: boolean;
  /** apiVersion the client expects (default SCHEMA_VERSION). */
  expectedApiVersion?: string;
}

/**
 * One-call convenience: spawn → wait for health → (optionally) version-check.
 * On any failure it shuts the sidecar down before rethrowing, so a failed start
 * never leaks a process.
 */
export async function startSidecar(opts: StartOptions): Promise<Sidecar> {
  const sidecar = spawnSidecar(opts);
  try {
    await waitForHealth(sidecar.baseUrl, { timeoutMs: opts.healthTimeoutMs });
    if (opts.verifyVersion ?? true) {
      await checkVersion(sidecar.client, {
        expectedApiVersion: opts.expectedApiVersion,
      });
    }
    return sidecar;
  } catch (e) {
    log.error(`startSidecar failed (${(e as Error).message}); shutting down`);
    await shutdown(sidecar).catch(() => undefined);
    throw e;
  }
}
