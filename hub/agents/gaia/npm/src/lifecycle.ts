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
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

import {
  BinaryNotFoundError,
  HealthTimeoutError,
  HttpError,
  IntegrityError,
  MalformedResponseError,
  PortInUseError,
  SidecarExitedError,
  VersionMismatchError,
} from "./errors.js";
import { createLogger } from "./logger.js";
import {
  type BinaryLock,
  type ComponentName,
  currentPlatformKey,
  isPlaceholderSha,
  loadLock,
  resolveEntry,
} from "./platform.js";

const log = createLogger("lifecycle");

export const DEFAULT_HOST = "127.0.0.1";

/** Matches `gaia_agent.server.DEFAULT_PORT`. NEVER 4001 (repo-reserved). */
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
  /**
   * Re-verify the file's SHA-256 against `binaries.lock.json` before handing
   * back a path that is about to be spawned. Default `true` — the cache dir is
   * predictable, so anything that can write it could otherwise get code run.
   * Set `false` only for a binary you built yourself, which no lock describes.
   */
  verify?: boolean;
  /** Pre-loaded lock, to avoid re-reading it per call. */
  lock?: BinaryLock;
}

/** SHA-256 of a file, read in chunks so a ~200MB binary is not buffered whole. */
function fileSha256Sync(filePath: string): string {
  const hash = crypto.createHash("sha256");
  const buf = Buffer.allocUnsafe(1 << 20);
  const fd = fs.openSync(filePath, "r");
  try {
    for (;;) {
      const n = fs.readSync(fd, buf, 0, buf.length, null);
      if (n === 0) break;
      hash.update(buf.subarray(0, n));
    }
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest("hex");
}

/**
 * Re-verify an on-disk binary against the lock. `fetch.ts` calls the SHA verify
 * "the security boundary"; this keeps that true for the resolve→spawn path,
 * which is exported and whose cache path is guessable.
 */
function verifyAgainstLock(
  full: string,
  component: ComponentName,
  lock: BinaryLock | undefined,
): void {
  const platformKey = currentPlatformKey();
  const entry = resolveEntry(lock ?? loadLock(), component, platformKey);
  if (isPlaceholderSha(entry.sha256)) {
    throw new IntegrityError(
      `binaries.lock.json has a placeholder sha256 for ${component}/'${platformKey}', ` +
        `so ${full} cannot be verified and must not be spawned. Install a released ` +
        "@amd-gaia/gaia, or pass { verify: false } if you built this binary yourself.",
    );
  }
  const actual = fileSha256Sync(full);
  if (actual.toLowerCase() !== entry.sha256.toLowerCase()) {
    throw new IntegrityError(
      `SHA-256 mismatch for the ${component} binary at ${full}:\n` +
        `  expected ${entry.sha256}\n` +
        `  actual   ${actual}\n` +
        "Refusing to spawn a binary that does not match binaries.lock.json. " +
        "Re-run `npx @amd-gaia/gaia fetch --force` to reinstall it; if it persists, " +
        "report it at https://github.com/amd/gaia/issues.",
    );
  }
}

/** Resolve a fetched binary's path, failing loudly if it is not there. */
function resolveIn(
  opts: ResolveOptions,
  component: ComponentName,
  fallback: string,
  what: string,
): string {
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
  if (opts.verify ?? true) verifyAgainstLock(full, component, opts.lock);
  return full;
}

export function resolveSidecarPath(opts: ResolveOptions): string {
  return resolveIn(opts, "sidecar", sidecarExecutableName(), "gaia-agent sidecar");
}

export function resolveTuiPath(opts: ResolveOptions): string {
  return resolveIn(opts, "tui", tuiExecutableName(), "gaia-tui");
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

/** stderr from an exit handler: console.error can be async on a piped stderr. */
function writeStderrSync(msg: string): void {
  try {
    fs.writeSync(2, msg.endsWith("\n") ? msg : `${msg}\n`);
  } catch {
    /* stderr unavailable */
  }
}

function killTreeSync(sidecar: Sidecar): void {
  const { child } = sidecar;
  if (child.pid === undefined) return;
  if (child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform === "win32") {
      const r = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: ["ignore", "ignore", "pipe"],
        encoding: "utf8",
      });
      // A refusal ("Access is denied") leaves the port held; say so now rather
      // than let it surface as an unexplained bind failure on the next run.
      if (r.error || r.status !== 0) {
        writeStderrSync(
          `[gaia:lifecycle] ERROR taskkill /PID ${child.pid} /T /F failed ` +
            `(${r.error ? r.error.message : `exit ${String(r.status)}`}): ` +
            `${(r.stderr ?? "").trim() || "(no output)"}. ` +
            `Kill pid ${child.pid} manually or port ${sidecar.port} stays bound.`,
        );
      }
    } else {
      process.kill(-child.pid, "SIGKILL");
    }
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "ESRCH") return; // already gone — the outcome we wanted
    writeStderrSync(
      `[gaia:lifecycle] ERROR could not kill the sidecar process group ` +
        `${String(child.pid)}: ${(e as Error).message}. ` +
        `Kill it manually or port ${sidecar.port} stays bound.`,
    );
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

/**
 * True when ours is the only listener for `event`, i.e. nothing else in this
 * process handles it and the process is therefore going down.
 *
 * Reaping is only ours to do in that case: a host with its own handler keeps
 * running, and killing its sidecar would turn an exception it handled into an
 * unexplained ECONNREFUSED on its next request.
 */
function weOwnTheExit(event: string): boolean {
  return process.listenerCount(event) === 1;
}

function installCleanupHandlers(): void {
  if (cleanupInstalled) return;
  cleanupInstalled = true;
  // The backstop: whatever route the process takes out, this runs.
  process.on("exit", reapAllSync);
  // crashHandler reaps before it exits, so the guard is the whole handler.
  process.on("uncaughtException", (err) => {
    if (weOwnTheExit("uncaughtException")) crashHandler(err);
  });
  process.on("unhandledRejection", (err) => {
    if (weOwnTheExit("unhandledRejection")) crashHandler(err);
  });
  for (const sig of CLEANUP_SIGNALS) {
    const handler = (): void => {
      if (!weOwnTheExit(sig)) return;
      reapAllSync();
      // Restore the default disposition and re-raise so we still terminate.
      process.removeListener(sig, handler);
      process.kill(process.pid, sig);
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

async function getJson<T>(
  url: string,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  // Link the caller's signal so an in-flight probe aborts the moment the
  // process being probed dies, rather than at the next poll.
  const onAbort = (): void => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener("abort", onAbort, { once: true });
  try {
    const res = await fetch(url, {
      headers: { accept: "application/json" },
      signal: controller.signal,
    });
    const text = await res.text();
    if (!res.ok) throw new HttpError(res.status, url, text);
    try {
      return JSON.parse(text) as T;
    } catch (e) {
      // A proxy or captive portal answering 200 with HTML lands here; a bare
      // SyntaxError would escape the CLI's GaiaError branch as a raw stack.
      throw new MalformedResponseError(
        `${url} returned HTTP ${res.status} but its body is not JSON ` +
          `(${(e as Error).message}). First 200 bytes: ${JSON.stringify(text.slice(0, 200))}. ` +
          "Something other than the gaia sidecar is answering that address — " +
          "check for a proxy on the port, or point at the right host/port.",
      );
    }
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

/** `GET /health` — liveness only; it does not mean a model is loaded. */
export function health(
  baseUrl: string,
  timeoutMs = 1000,
  signal?: AbortSignal,
): Promise<HealthResponse> {
  return getJson<HealthResponse>(`${baseUrl}/health`, timeoutMs, signal);
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
  // Re-checked after every await, not just at the top of the loop: a probe that
  // races the process's death would otherwise report a foreign server's health.
  const throwIfAborted = (): void => {
    if (!opts.signal?.aborted) return;
    throw new HealthTimeoutError(
      `health wait for ${baseUrl} was aborted after ${attempts} probe(s) ` +
        "(the process being probed exited).",
    );
  };
  while (Date.now() < deadline) {
    throwIfAborted();
    attempts++;
    try {
      const h = await health(baseUrl, intervalMs * 4, opts.signal);
      throwIfAborted();
      if (h.status === "ok") {
        log.debug(`sidecar healthy after ${attempts} probe(s)`);
        return;
      }
      lastErr = `unexpected health status: ${JSON.stringify(h)}`;
    } catch (e) {
      throwIfAborted();
      lastErr = (e as Error).message;
    }
    await sleep(intervalMs);
    throwIfAborted();
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
  if (child.exitCode !== null || child.signalCode !== null || child.pid === undefined) {
    liveSidecars.delete(sidecar);
    log.debug("shutdown: sidecar already exited");
    return;
  }
  const pid = child.pid;
  log.info(`shutting down sidecar pid=${pid} (tree-kill)`);

  const exited = new Promise<void>((resolve) => {
    child.once("exit", () => resolve());
  });

  // Why the kill was refused, kept for the throw below — "Access is denied" is
  // the difference between "retry" and "run this elevated".
  let killDiagnostic = "";

  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
      stdio: ["ignore", "ignore", "pipe"],
    });
    let taskkillErr = "";
    killer.stderr?.on("data", (d) => {
      taskkillErr += String(d);
    });
    killer.on("error", (e) => {
      killDiagnostic = `taskkill could not be launched: ${e.message}`;
      log.error(killDiagnostic);
    });
    killer.on("exit", (code) => {
      if (code === 0) return;
      killDiagnostic =
        `taskkill /PID ${pid} /T /F exited ${String(code)}: ` +
        `${taskkillErr.trim() || "(no output)"}`;
      log.error(killDiagnostic);
    });
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
      // Deliberately still registered: the process-exit reaper is the last
      // chance to reap a survivor, and de-registering here would orphan it.
      throw new Error(
        `the gaia sidecar (pid ${pid}) did not exit after a forced kill` +
          (killDiagnostic ? ` (${killDiagnostic})` : "") +
          ". Kill it manually — " +
          (process.platform === "win32"
            ? `taskkill /PID ${pid} /T /F`
            : `kill -9 -${pid}`) +
          ` — or port ${sidecar.port} stays bound.`,
      );
    }
  }
  liveSidecars.delete(sidecar);
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
 * Refuse a handle whose own child is dead. A healthy `/health` proves *something*
 * owns the port — this proves it is ours. Without it a second `gaia serve` would
 * print a ready URL for a server it cannot shut down.
 */
function assertOurs(sidecar: Sidecar): void {
  const { child } = sidecar;
  if (child.exitCode === null && child.signalCode === null) return;
  throw new SidecarExitedError(
    `the gaia sidecar we spawned exited (code=${String(child.exitCode)} ` +
      `signal=${String(child.signalCode)}) while ${sidecar.baseUrl}/health still ` +
      `answered — another process is already bound to port ${sidecar.port}, most ` +
      "likely an instance you started earlier. Stop it (" +
      (process.platform === "win32"
        ? `netstat -ano | findstr :${sidecar.port}`
        : `lsof -i :${sidecar.port}`) +
      ") or start on a different port with --port. Re-run with DEBUG=gaia to see " +
      "the sidecar's own output.",
  );
}

/**
 * Decide what a dead child actually means, by asking who owns the port now.
 *
 * The health wait aborts the instant our child exits, so on a fast machine that
 * abort can beat a healthy reply from an incumbent — and then a port conflict
 * reports as a plain timeout. Re-probing settles which failure this is instead
 * of letting the race name it. Silent when our child is alive, or when nothing
 * answers: that is a genuine timeout and the caller rethrows it.
 */
async function assertNotAForeignServer(sidecar: Sidecar): Promise<void> {
  const { child } = sidecar;
  if (child.exitCode === null && child.signalCode === null) return;
  try {
    if ((await health(sidecar.baseUrl, 1_000)).status !== "ok") return;
  } catch {
    return; // nothing is listening — our sidecar simply died
  }
  assertOurs(sidecar); // something else owns the port; throws SidecarExitedError
}

/**
 * True when something is already listening on host:port.
 *
 * A TCP connect, not a `/health` probe: ANY listener makes our bind fail, and
 * the incumbent need not be a GAIA sidecar. Unreachable-in-time counts as free —
 * this exists to turn a confusing failure into a clear one early, and the
 * spawn + health wait remains the actual gate.
 */
function portInUse(host: string, port: number, timeoutMs = 500): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    const settle = (inUse: boolean): void => {
      socket.destroy();
      resolve(inUse);
    };
    socket.setTimeout(timeoutMs, () => settle(false));
    socket.once("connect", () => settle(true));
    socket.once("error", () => settle(false)); // ECONNREFUSED — nothing there
  });
}

/**
 * Spawn → wait for health → assert the child is still ours → (optionally)
 * version-check. On any failure the sidecar is shut down before rethrowing, so a
 * failed start never leaks a process.
 *
 * The port is checked BEFORE the spawn. Without that, the frozen sidecar spends
 * seconds unpacking before it even attempts its bind, while an incumbent answers
 * `/health` in milliseconds — so the health wait succeeds, the still-unpacking
 * child looks alive, and we would hand back a handle for a server we do not own.
 * `assertOurs` / `assertNotAForeignServer` stay as backstops for the narrower
 * race where something binds after this check.
 */
export async function startSidecar(opts: StartOptions): Promise<Sidecar> {
  const host = opts.host ?? DEFAULT_HOST;
  const port = opts.port ?? DEFAULT_PORT;
  if (await portInUse(host, port)) {
    throw new PortInUseError(
      `port ${port} on ${host} is already in use, so the gaia sidecar cannot bind ` +
        "it. Most likely an instance you started earlier is still running. Find it " +
        "with " +
        (process.platform === "win32"
          ? `\`netstat -ano | findstr :${port}\``
          : `\`lsof -i :${port}\``) +
        `, then stop it — or start on a different port with --port. Nothing was ` +
        "spawned.",
    );
  }
  const sidecar = spawnSidecar(opts);
  // A sidecar that dies immediately (missing runtime lib, port already bound)
  // must not make the caller wait out the full health timeout.
  const died = new AbortController();
  sidecar.child.once("exit", () => died.abort());
  try {
    try {
      await waitForHealth(sidecar.baseUrl, {
        timeoutMs: opts.healthTimeoutMs,
        signal: died.signal,
      });
    } catch (e) {
      await assertNotAForeignServer(sidecar);
      throw e;
    }
    assertOurs(sidecar);
    if (opts.verifyVersion ?? true) {
      await checkVersion(sidecar.baseUrl, {
        expectedApiVersion: opts.expectedApiVersion,
      });
      assertOurs(sidecar);
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
