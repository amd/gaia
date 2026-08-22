// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Process lifecycle for the two binaries this package installs.
 *
 * **Sidecar** — locate the frozen binary, spawn it, poll `GET /health`, check the
 * contract version via `GET /version`, and shut it down killing the whole process
 * tree. Tree-kill matters: a PyInstaller one-file build spawns a child uvicorn
 * process that `child.kill()` on the parent does NOT reap, leaving the port held.
 *
 * **TUI** — exec it with stdio inherited and propagate its exit code, so the
 * terminal UI owns the terminal completely.
 *
 * Note on who spawns the sidecar in the normal `gaia` flow: the GAIA daemon does.
 * The TUI reaches agents through the daemon's relay and deliberately never holds a
 * sidecar bearer token, so `gaia run` stages the *verified* sidecar binary into the
 * daemon's cache and lets the daemon own its process. The helpers below are the
 * direct path — used by `gaia serve` and by programmatic integrators who want the
 * REST surface without a daemon.
 */

import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import {
  BinaryNotFoundError,
  HealthTimeoutError,
  HttpError,
  VersionMismatchError,
} from "./errors.js";
import { createLogger } from "./logger.js";
import { currentPlatformKey } from "./platform.js";

const log = createLogger("lifecycle");

export const DEFAULT_HOST = "127.0.0.1";

/** Matches `gaia_agent_gaia.server.DEFAULT_PORT`. NEVER 4001 (repo-reserved). */
export const DEFAULT_PORT = 8141;

/** Repo-wide reserved port. Nothing here may ever bind it. */
export const RESERVED_PORT = 4001;

/** The apiVersion this package is built against (`server.py: API_VERSION`). */
export const API_VERSION = "2.12";

/** The agent id in the sidecar's route prefix (`/v1/gaia/...`). */
export const AGENT_ID = "gaia";

/** `GET /health` response. */
export interface HealthResponse {
  status: string;
  service?: string;
}

/** `GET /version` response — the key names are a contract, not a convention. */
export interface VersionResponse {
  apiVersion: string;
  agentVersion: string;
}

/** Basename the sidecar executable is written as (matches the lock). */
export function sidecarExecutableName(
  platform: NodeJS.Platform = process.platform,
): string {
  return platform === "win32" ? "gaia-agent.exe" : "gaia-agent";
}

/**
 * Basename the TUI executable is written as. Deliberately `gaia-tui`, not
 * `gaia`: npm installs its own `gaia` bin shim, and a same-named executable in a
 * cache dir on PATH would shadow it.
 */
export function tuiExecutableName(
  platform: NodeJS.Platform = process.platform,
): string {
  return platform === "win32" ? "gaia-tui.exe" : "gaia-tui";
}

export interface ResolveOptions {
  /** Directory the binary was fetched into. */
  resourcesDir: string;
  /** Override the executable basename (defaults per-platform). */
  executable?: string;
}

/** Resolve a fetched binary's path, failing loudly if it is not there. */
function resolveIn(opts: ResolveOptions, fallback: string, what: string): string {
  if (!opts?.resourcesDir) {
    throw new TypeError("resolve requires { resourcesDir }");
  }
  const full = path.resolve(opts.resourcesDir, opts.executable ?? fallback);
  if (!fs.existsSync(full)) {
    throw new BinaryNotFoundError(
      `${what} binary not found at ${full} (platform ${currentPlatformKey()}). ` +
        "Run the fetch step first: `npx @amd-gaia/gaia fetch`.",
    );
  }
  return full;
}

export function resolveSidecarPath(opts: ResolveOptions): string {
  return resolveIn(opts, sidecarExecutableName(), "gaia-agent sidecar");
}

export function resolveTuiPath(opts: ResolveOptions): string {
  return resolveIn(opts, tuiExecutableName(), "gaia-tui");
}

export interface SpawnOptions {
  /** Absolute path to the sidecar binary. */
  binaryPath: string;
  /** Bind host. Default 127.0.0.1. */
  host?: string;
  /** Bind port. Default 8141. NEVER 4001. */
  port?: number;
  /** Extra CLI args appended verbatim. */
  extraArgs?: string[];
  /** Extra env vars merged over process.env. */
  env?: NodeJS.ProcessEnv;
  /**
   * Reap this sidecar if the parent exits, crashes, or is interrupted without an
   * explicit `shutdown()`. Default `true` — the frozen binary's detached child
   * must never outlive us holding the port. Set `false` to own the lifecycle.
   */
  autoCleanup?: boolean;
}

/** A running sidecar handle. */
export interface Sidecar {
  child: ChildProcess;
  host: string;
  port: number;
  baseUrl: string;
}

// --- Auto-cleanup: reap orphaned sidecars when the parent process goes away ---
// The sidecar is spawned detached (its own process group), so a parent Ctrl+C,
// crash, or plain exit does NOT propagate to it — without this it keeps running
// and holds its port. Handlers are installed once and SIGKILL the tree
// synchronously on the way out. A hard SIGKILL of the parent is the one case no
// in-process handler can catch.
const liveSidecars = new Set<Sidecar>();
let cleanupInstalled = false;
const CLEANUP_SIGNALS: NodeJS.Signals[] = ["SIGINT", "SIGTERM", "SIGHUP"];

function killTreeSync(sidecar: Sidecar): void {
  const { child } = sidecar;
  if (child.pid === undefined) return;
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-child.pid, "SIGKILL");
    }
  } catch {
    /* already gone */
  }
}

function reapAllSync(): void {
  for (const s of liveSidecars) killTreeSync(s);
  liveSidecars.clear();
}

function crashHandler(err: unknown): void {
  reapAllSync();
  try {
    // Synchronous write — console.error can truncate on a piped stderr before
    // process.exit flushes.
    fs.writeSync(
      2,
      `${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`,
    );
  } catch {
    /* stderr unavailable */
  }
  process.exit(1);
}

function installCleanupHandlers(): void {
  if (cleanupInstalled) return;
  cleanupInstalled = true;
  process.on("exit", reapAllSync);
  process.on("uncaughtException", (err) => {
    reapAllSync();
    if (process.listenerCount("uncaughtException") === 1) crashHandler(err);
  });
  process.on("unhandledRejection", (err) => {
    reapAllSync();
    if (process.listenerCount("unhandledRejection") === 1) crashHandler(err);
  });
  for (const sig of CLEANUP_SIGNALS) {
    const handler = (): void => {
      reapAllSync();
      // Sole listener → restore the default disposition and re-raise so the
      // process still terminates (Ctrl+C).
      if (process.listenerCount(sig) === 1) {
        process.removeListener(sig, handler);
        process.kill(process.pid, sig);
      }
    };
    process.on(sig, handler);
  }
}

function registerForCleanup(sidecar: Sidecar): void {
  installCleanupHandlers();
  liveSidecars.add(sidecar);
  sidecar.child.once("exit", () => liveSidecars.delete(sidecar));
}

/**
 * Spawn the frozen sidecar. Does NOT wait for readiness — call `waitForHealth`
 * (or `startSidecar`, which does both).
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
  if (port === RESERVED_PORT) {
    throw new RangeError(`port ${RESERVED_PORT} is reserved and must never be used`);
  }
  const args = ["--host", host, "--port", String(port)];
  if (opts.extraArgs?.length) args.push(...opts.extraArgs);

  log.info(`spawning ${opts.binaryPath} ${args.join(" ")}`);

  const child = spawn(opts.binaryPath, args, {
    // detached on POSIX → the child leads its own process group so we can signal
    // the whole tree. Windows has different detach semantics; we use taskkill /T.
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

  const sidecar: Sidecar = { child, host, port, baseUrl: `http://${host}:${port}` };
  if (opts.autoCleanup !== false) registerForCleanup(sidecar);
  return sidecar;
}

async function getJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      headers: { accept: "application/json" },
      signal: controller.signal,
    });
    const text = await res.text();
    if (!res.ok) throw new HttpError(res.status, url, text);
    return JSON.parse(text) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** `GET /health` — liveness only; it does not mean a model is loaded. */
export function health(baseUrl: string, timeoutMs = 1000): Promise<HealthResponse> {
  return getJson<HealthResponse>(`${baseUrl}/health`, timeoutMs);
}

/** `GET /version` — the contract probe (`{ apiVersion, agentVersion }`). */
export function version(baseUrl: string, timeoutMs = 5000): Promise<VersionResponse> {
  return getJson<VersionResponse>(`${baseUrl}/version`, timeoutMs);
}

export interface WaitForHealthOptions {
  /** Total time to wait before failing loudly. Default 60000ms. */
  timeoutMs?: number;
  /** Poll interval. Default 250ms. */
  intervalMs?: number;
  /** Abort the wait early (e.g. the process being probed died). */
  signal?: AbortSignal;
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/**
 * Poll `GET /health` until the sidecar reports ok, or throw `HealthTimeoutError`.
 * Never silently assumes ready.
 */
export async function waitForHealth(
  baseUrl: string,
  opts: WaitForHealthOptions = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? 60_000;
  const intervalMs = opts.intervalMs ?? 250;
  const deadline = Date.now() + timeoutMs;
  let lastErr = "";
  let attempts = 0;
  while (Date.now() < deadline) {
    if (opts.signal?.aborted) {
      throw new HealthTimeoutError(
        `health wait for ${baseUrl} was aborted after ${attempts} probe(s) ` +
          "(the process being probed exited).",
      );
    }
    attempts++;
    try {
      const h = await health(baseUrl, intervalMs * 4);
      if (h.status === "ok") {
        log.debug(`sidecar healthy after ${attempts} probe(s)`);
        return;
      }
      lastErr = `unexpected health status: ${JSON.stringify(h)}`;
    } catch (e) {
      lastErr = (e as Error).message;
    }
    await sleep(intervalMs);
  }
  throw new HealthTimeoutError(
    `the gaia sidecar at ${baseUrl} did not become healthy within ${timeoutMs}ms ` +
      `(${attempts} probes). Last error: ${lastErr}. ` +
      "Re-run with DEBUG=gaia to see the sidecar's own output, and check the port is free.",
  );
}

/** Parse "2.12" → 2 (major). Throws on a non-numeric major. */
function majorOf(v: string): number {
  const major = Number.parseInt(String(v).split(".")[0] ?? "", 10);
  if (Number.isNaN(major)) {
    throw new VersionMismatchError(`cannot parse apiVersion major from '${v}'`);
  }
  return major;
}

export interface VersionCheckOptions {
  /** apiVersion this package was built against. Default API_VERSION ("2.12"). */
  expectedApiVersion?: string;
}

/**
 * Fetch `/version` and refuse a sidecar whose apiVersion MAJOR differs from what
 * this package expects. A major bump is a breaking contract change; a higher
 * minor (same major) is a backward-compatible addition and is accepted.
 */
export async function checkVersion(
  baseUrl: string,
  opts: VersionCheckOptions = {},
): Promise<VersionResponse> {
  const expected = opts.expectedApiVersion ?? API_VERSION;
  const info = await version(baseUrl);
  const expectedMajor = majorOf(expected);
  const actualMajor = majorOf(info.apiVersion);
  if (actualMajor !== expectedMajor) {
    throw new VersionMismatchError(
      `incompatible gaia sidecar apiVersion: it reports '${info.apiVersion}' ` +
        `(major ${actualMajor}) but this package expects major ${expectedMajor} ` +
        `('${expected}'). A major bump is a breaking contract change. ` +
        "Upgrade @amd-gaia/gaia to a version matching the sidecar.",
    );
  }
  log.debug(`version OK: apiVersion=${info.apiVersion} agentVersion=${info.agentVersion}`);
  return info;
}

/**
 * Shut the sidecar down, killing the whole process tree. Resolves once the
 * process has exited (or immediately if it already had).
 */
export async function shutdown(sidecar: Sidecar, timeoutMs = 5000): Promise<void> {
  const { child } = sidecar;
  liveSidecars.delete(sidecar); // an explicit shutdown owns the lifecycle now
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
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      stdio: "ignore",
    });
    killer.on("error", (e) => log.error(`taskkill failed: ${e.message}`));
  } else {
    try {
      process.kill(-pid, "SIGTERM"); // negative pid → the whole process group
    } catch (e) {
      log.debug(`SIGTERM to group failed (${(e as Error).message}); trying direct`);
      try {
        child.kill("SIGTERM");
      } catch {
        /* already gone */
      }
    }
  }

  const raceExit = async (ms: number): Promise<"exited" | "timeout"> => {
    let t: NodeJS.Timeout;
    const timer = new Promise<"timeout">((resolve) => {
      t = setTimeout(() => resolve("timeout"), ms);
    });
    return Promise.race([exited.then(() => "exited" as const), timer]).finally(() =>
      clearTimeout(t),
    );
  };

  if ((await raceExit(timeoutMs)) === "timeout") {
    log.warn(`sidecar did not exit within ${timeoutMs}ms; forcing`);
    if (process.platform !== "win32") {
      try {
        process.kill(-pid, "SIGKILL");
      } catch {
        /* gone */
      }
    }
    // Bound the final wait too. On Windows there is no second escalation after
    // taskkill, so an unbounded await here would hang Ctrl+C forever.
    if ((await raceExit(timeoutMs)) === "timeout") {
      throw new Error(
        `the gaia sidecar (pid ${pid}) did not exit after a forced kill. ` +
          "Kill it manually — " +
          (process.platform === "win32"
            ? `taskkill /PID ${pid} /T /F`
            : `kill -9 -${pid}`) +
          ` — or the port it holds stays bound.`,
      );
    }
  }
  log.info("sidecar shut down");
}

export interface StartOptions extends SpawnOptions {
  /** Health-wait timeout. Default 60000ms (a cold frozen binary unpacks first). */
  healthTimeoutMs?: number;
  /** Verify the contract apiVersion after health. Default true. */
  verifyVersion?: boolean;
  /** apiVersion this package expects. Default API_VERSION. */
  expectedApiVersion?: string;
}

/**
 * Spawn → wait for health → (optionally) version-check. On any failure the
 * sidecar is shut down before rethrowing, so a failed start never leaks a
 * process.
 */
export async function startSidecar(opts: StartOptions): Promise<Sidecar> {
  const sidecar = spawnSidecar(opts);
  // A sidecar that dies immediately (missing runtime lib, port already bound)
  // must not make the caller wait out the full health timeout.
  const died = new AbortController();
  sidecar.child.once("exit", () => died.abort());
  try {
    await waitForHealth(sidecar.baseUrl, {
      timeoutMs: opts.healthTimeoutMs,
      signal: died.signal,
    });
    if (opts.verifyVersion ?? true) {
      await checkVersion(sidecar.baseUrl, {
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

export interface RunTuiOptions {
  /** Absolute path to the gaia-tui binary. */
  binaryPath: string;
  /** Args forwarded verbatim to the TUI. */
  args?: string[];
  /** Extra env merged over process.env. */
  env?: NodeJS.ProcessEnv;
}

/**
 * Run the TUI in the foreground with stdio inherited and resolve with its exit
 * code. A signal-terminated TUI resolves as 128 + signum, the shell convention,
 * so a Ctrl+C is distinguishable from a clean exit 0.
 */
export function runTui(opts: RunTuiOptions): Promise<number> {
  if (!opts?.binaryPath) {
    throw new TypeError("runTui requires { binaryPath }");
  }
  if (!fs.existsSync(opts.binaryPath)) {
    throw new BinaryNotFoundError(`gaia-tui binary does not exist: ${opts.binaryPath}`);
  }
  return new Promise<number>((resolve, reject) => {
    const child = spawn(opts.binaryPath, opts.args ?? [], {
      stdio: "inherit",
      env: { ...process.env, ...(opts.env ?? {}) },
    });
    child.on("error", (e) =>
      reject(
        new BinaryNotFoundError(
          `could not launch the TUI at ${opts.binaryPath}: ${e.message}`,
        ),
      ),
    );
    child.on("exit", (code, signal) => {
      if (signal) {
        resolve(128 + (SIGNUM[signal] ?? 0));
        return;
      }
      resolve(code ?? 0);
    });
  });
}

// Only the signals a foreground TUI realistically dies from; anything else maps
// to 128, which still reads as "terminated by a signal".
const SIGNUM: Record<string, number> = {
  SIGHUP: 1,
  SIGINT: 2,
  SIGQUIT: 3,
  SIGKILL: 9,
  SIGTERM: 15,
};
