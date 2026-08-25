// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Binary fetcher for both components (`sidecar` and `tui`).
 *
 * Resolves the current platform → looks the component up in
 * `binaries.lock.json` → downloads it from THAT COMPONENT's base URL
 * (overridable) → **verifies its SHA-256 against the lock and fails loudly on
 * any mismatch** → writes it into a cache dir → `chmod +x` on POSIX.
 *
 * The two components come from different hub lanes: the sidecar from
 * `agents/gaia/<agentVersion>/`, the TUI from `agents/terminal-hub/<tuiVersion>/`
 * (the same binary a core install runs as `gaia tui`). Each entry's base URL
 * comes from its own component, never from a shared top-level one.
 *
 * The SHA verify is the security boundary: a tampered, truncated, or
 * not-yet-published artifact is rejected before it can ever be spawned. There is
 * NO "use it anyway" path, and a placeholder hash in the lock blocks the fetch
 * outright rather than degrading to an unverified download.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { IntegrityError, PlatformError } from "./errors.js";
import { createLogger } from "./logger.js";
import { joinUrl } from "./url.js";
import {
  type BinaryLock,
  type BinaryLockEntry,
  type ComponentName,
  componentBaseUrl,
  componentLock,
  currentPlatformKey,
  defaultLockPath,
  isPlaceholderSha,
  loadLock,
  resolveEntry,
} from "./platform.js";

const log = createLogger("fetch");

/**
 * Where verified binaries are cached, keyed by agent version so a version bump
 * never reuses the previous release's executables.
 */
export function defaultCacheDir(agentVersion: string): string {
  return path.join(os.homedir(), ".gaia", "npm-cache", `gaia-${agentVersion}`);
}

/** The hub agent id this package installs the sidecar as. */
export const SIDECAR_AGENT_ID = "gaia";

/** Basename of the hub install record (`gaia.hub.installer.SENTINEL_NAME`). */
export const INSTALLED_SENTINEL_NAME = ".installed";

/**
 * The daemon's own sidecar cache — `~/.gaia/agents/gaia/`, mirroring
 * `gaia.daemon.sidecars.fetch.default_cache_dir("gaia")`. Staging the verified
 * sidecar here means the daemon's own fetch is a SHA-256 cache hit instead of a
 * second download. This path is a cross-repo contract with the daemon.
 */
export function daemonSidecarCacheDir(agentId = SIDECAR_AGENT_ID): string {
  return path.join(os.homedir(), ".gaia", "agents", agentId);
}

/**
 * Record a completed install the way `gaia.hub.installer` does.
 *
 * Staging a verified binary is only half the job the CLI claims to do. Without
 * this file the daemon's `_hub_installed_binary` sees no install, and the TUI
 * treats `gaia-agent` as its own stdio child — spawning our REST sidecar over
 * stdio and filling the chat with uvicorn's startup log. With it, the TUI uses
 * daemon transport and the daemon supervises the process and mints its token.
 *
 * Field names and `artifact_kind`/`executable`/`artifact_sha256` are a
 * cross-repo contract with `InstalledAgent.to_dict()`; the daemon re-hashes the
 * binary and raises if it does not match `artifact_sha256`, so this must carry
 * the hash we actually verified.
 */
async function writeInstalledSentinel(opts: {
  outDir: string;
  version: string;
  sha256: string;
  executable: string;
}): Promise<void> {
  const sentinel = path.join(opts.outDir, INSTALLED_SENTINEL_NAME);
  const record = {
    id: SIDECAR_AGENT_ID,
    version: opts.version,
    language: "python",
    installed_at: new Date().toISOString(),
    artifact_sha256: opts.sha256,
    path: opts.outDir,
    artifact_kind: "binary",
    executable: opts.executable,
  };
  // Temp-then-rename: a truncated sentinel reads as a corrupt install, which
  // looks identical to "never installed".
  const tmp = `${sentinel}.tmp.${process.pid}`;
  try {
    await fsp.writeFile(tmp, `${JSON.stringify(record, null, 2)}\n`, "utf8");
    await fsp.rename(tmp, sentinel);
  } catch (e) {
    await fsp.rm(tmp, { force: true }).catch(() => undefined);
    throw new Error(
      `verified the sidecar at ${opts.outDir} but could not write its ` +
        `${INSTALLED_SENTINEL_NAME} record: ${(e as Error).message}. Without it the ` +
        "daemon does not see a completed install and the TUI falls back to running " +
        `the sidecar over stdio. Check write permissions on ${opts.outDir}.`,
      { cause: e },
    );
  }
  log.debug(`wrote ${sentinel} (v${opts.version})`);
}

export interface FetchOptions {
  /** Which binary to fetch. */
  component: ComponentName;
  /** Directory the verified binary is written into. Required. */
  outDir: string;
  /**
   * Override this component's `baseUrl` (e.g. a local mirror). Trailing slash
   * optional. Applies to whichever component is being fetched — the two lanes'
   * filenames never collide, so one flat mirror directory can serve both.
   */
  baseUrl?: string;
  /** Override the platform key (defaults to the current host). */
  platformKey?: string;
  /** Path to the lock file (defaults to the packaged binaries.lock.json). */
  lockPath?: string;
  /** A pre-loaded lock, to avoid re-reading it per component. */
  lock?: BinaryLock;
  /** Fetch override (tests). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Re-download even when a verified binary is already cached. Default false. */
  force?: boolean;
  /** Abort the download after this many ms. Default 300000 (the sidecar is ~200MB). */
  timeoutMs?: number;
}

export interface FetchResult {
  component: ComponentName;
  /** Absolute path to the written, verified executable. */
  binaryPath: string;
  /** The platform key resolved. */
  platformKey: string;
  /** The verified SHA-256 (lowercase hex). */
  sha256: string;
  /** Source URL the artifact was downloaded from. */
  url: string;
  /** True when the on-disk binary was reused (hash already matched). */
  cached: boolean;
}

function sha256Hex(buf: Buffer): string {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * SHA-256 of a file on disk, or null when it is simply absent. Any other error
 * (a permission problem, a directory in the way) is re-raised: reading it as
 * "no cache" would trigger a re-download that then fails on write with a far
 * less useful message.
 */
export async function fileSha256(filePath: string): Promise<string | null> {
  try {
    return sha256Hex(await fsp.readFile(filePath));
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return null;
    throw new Error(
      `cannot read the cached binary at ${filePath} to check its hash: ${(e as Error).message}. ` +
        "Fix the permissions on that path, or pass a different --cache-dir / --sidecar-dir.",
      { cause: e },
    );
  }
}

/**
 * Stream a response body to `tmp`, hashing as it goes.
 *
 * Incremental on purpose: the sidecar is ~200MB and `arrayBuffer()` would peak
 * at roughly double that. Awaiting each write applies backpressure.
 */
async function streamToFile(
  res: Response,
  tmp: string,
  url: string,
): Promise<{ sha256: string; bytes: number }> {
  if (!res.body) {
    throw new Error(
      `download failed: ${url} returned HTTP ${res.status} with no response body. ` +
        "Check the base URL and that the artifact is published for this platform.",
    );
  }
  const hash = crypto.createHash("sha256");
  let bytes = 0;
  let fh: fsp.FileHandle;
  try {
    fh = await fsp.open(tmp, "w");
  } catch (e) {
    throw new Error(
      `cannot open ${tmp} to stage the download: ${(e as Error).message}. ` +
        "Check write permissions on that directory, or pass a different " +
        "--sidecar-dir / --cache-dir.",
      { cause: e },
    );
  }
  try {
    for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
      hash.update(chunk);
      bytes += chunk.byteLength;
      await fh.write(chunk);
    }
  } finally {
    await fh.close();
  }
  return { sha256: hash.digest("hex"), bytes };
}

/**
 * Compare an already-computed SHA-256 with the expected one. Throws
 * `IntegrityError` loudly on mismatch — this is the no-silent-fallback gate.
 */
function assertSha256(actual: string, expected: string, sourceLabel: string): string {
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new IntegrityError(
      `SHA-256 mismatch for ${sourceLabel}:\n` +
        `  expected ${expected}\n` +
        `  actual   ${actual}\n` +
        "Refusing to use a binary that does not match binaries.lock.json. " +
        "The download may be corrupt, truncated, or tampered with. Re-run the fetch; " +
        "if it persists, the lock is stale relative to the published artifact — " +
        "report it at https://github.com/amd/gaia/issues.",
    );
  }
  return actual;
}

/** Verify an in-memory buffer against an expected SHA-256. */
export function verifySha256(
  buf: Buffer,
  expected: string,
  sourceLabel: string,
): string {
  return assertSha256(sha256Hex(buf), expected, sourceLabel);
}

/**
 * Fetch + verify + install one component's binary for the current platform.
 *
 * @throws PlatformError   unsupported platform / incomplete entry / placeholder hash
 * @throws IntegrityError  SHA-256 mismatch
 * @throws Error           download/network failure (HTTP status surfaced)
 */
export async function fetchBinary(opts: FetchOptions): Promise<FetchResult> {
  if (!opts?.outDir) {
    throw new TypeError("fetchBinary requires an outDir to write the binary into");
  }
  if (!opts?.component) {
    throw new TypeError('fetchBinary requires a component ("sidecar" or "tui")');
  }
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new TypeError("global fetch unavailable — use Node >= 18 or pass fetchImpl");
  }

  const lock: BinaryLock = opts.lock ?? loadLock(opts.lockPath ?? defaultLockPath());
  const platformKey = opts.platformKey ?? currentPlatformKey();
  const entry: BinaryLockEntry = resolveEntry(lock, opts.component, platformKey);
  // Per-component: the sidecar and the TUI live in different hub lanes at
  // different versions, so there is no single base URL to fall back to.
  const baseUrl = opts.baseUrl ?? componentBaseUrl(lock, opts.component);

  if (isPlaceholderSha(entry.sha256)) {
    throw new PlatformError(
      `binaries.lock.json has a placeholder sha256 for ${opts.component}/'${platformKey}' ` +
        `(${entry.sha256}), so no verifiable binary is published for it in this build. ` +
        "Fetch is blocked so an unverifiable binary can never be trusted. " +
        "Install a released @amd-gaia/gaia, or build the binary locally " +
        "(hub/agents/gaia/python/packaging for the sidecar, `make -C tui cross-compile` " +
        "for the TUI) and point the lifecycle helpers at it directly.",
    );
  }

  const outDir = path.resolve(opts.outDir);
  await fsp.mkdir(outDir, { recursive: true });
  const binaryPath = path.join(outDir, entry.executable);
  const url = joinUrl(baseUrl, entry.filename);

  if (!opts.force) {
    const existing = await fileSha256(binaryPath);
    if (existing && existing.toLowerCase() === entry.sha256.toLowerCase()) {
      log.debug(`cache hit: ${binaryPath} already matches lock sha256`);
      // Re-apply the exec bit: an interrupted earlier run or a restrictive umask
      // can leave correct bytes that still cannot be spawned.
      if (process.platform !== "win32") await fsp.chmod(binaryPath, 0o755);
      // Also on a cache hit: an earlier release staged a verified binary with no
      // sentinel, and those installs would otherwise never repair themselves.
      if (opts.component === "sidecar") {
        await writeInstalledSentinel({
          outDir,
          version: componentLock(lock, "sidecar").componentVersion,
          sha256: existing,
          executable: entry.executable,
        });
      }
      return {
        component: opts.component,
        binaryPath,
        platformKey,
        sha256: existing,
        url,
        cached: true,
      };
    }
  }

  log.info(`downloading ${opts.component} for ${platformKey} from ${url}`);
  const timeoutMs = opts.timeoutMs ?? 300_000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  // Streamed to a temp file while hashing incrementally: the sidecar is ~200MB
  // and buffering it whole peaks at roughly double that in RSS.
  const tmp = `${binaryPath}.download.${process.pid}`;
  let actualSha: string;
  let bytes: number;
  try {
    const res = await fetchImpl(url, {
      headers: { accept: "application/octet-stream" },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(
        `download failed: HTTP ${res.status} ${res.statusText} for ${url}. ` +
          "Check the base URL and that the artifact is published for this platform.",
      );
    }
    ({ sha256: actualSha, bytes } = await streamToFile(res, tmp, url));
  } catch (e) {
    await fsp.rm(tmp, { force: true }).catch(() => undefined);
    if ((e as Error).name === "AbortError") {
      throw new Error(`download timed out after ${timeoutMs}ms for ${url}`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
  log.debug(`downloaded ${bytes} bytes`);

  // Verify BEFORE the rename: unverified bytes must never reach the final path.
  let sha: string;
  try {
    sha = assertSha256(actualSha, entry.sha256, `${opts.component} ${platformKey} (${url})`);
  } catch (e) {
    await fsp.rm(tmp, { force: true }).catch(() => undefined);
    throw e;
  }

  try {
    await fsp.rename(tmp, binaryPath);
  } catch (e) {
    await fsp.rm(tmp, { force: true }).catch(() => undefined);
    throw new Error(
      `downloaded and verified the ${opts.component}, but could not move it into place ` +
        `at ${binaryPath}: ${(e as Error).message}. ` +
        "The usual cause is that the file is in use — stop any running gaia sidecar " +
        "or TUI (`gaia kill`) and re-run. Otherwise check write permissions on " +
        `${outDir}, or pass a different --sidecar-dir / --cache-dir.`,
      { cause: e },
    );
  }

  try {
    if (process.platform !== "win32") await fsp.chmod(binaryPath, 0o755);
  } catch (e) {
    throw new Error(
      `installed the ${opts.component} at ${binaryPath} but could not make it ` +
        `executable: ${(e as Error).message}. Run \`chmod +x ${binaryPath}\`, or ` +
        "pass a --sidecar-dir / --cache-dir you own.",
      { cause: e },
    );
  }

  if (opts.component === "sidecar") {
    await writeInstalledSentinel({
      outDir,
      version: componentLock(lock, "sidecar").componentVersion,
      sha256: sha,
      executable: entry.executable,
    });
  }

  log.info(`installed verified ${opts.component} -> ${binaryPath}`);
  return {
    component: opts.component,
    binaryPath,
    platformKey,
    sha256: sha,
    url,
    cached: false,
  };
}

export interface FetchAllOptions extends Omit<FetchOptions, "component" | "outDir"> {
  /** Cache dir for the TUI. Defaults to `defaultCacheDir(lock.agentVersion)`. */
  tuiDir?: string;
  /** Cache dir for the sidecar. Defaults to the daemon's `~/.gaia/agents/gaia`. */
  sidecarDir?: string;
}

export interface FetchAllResult {
  sidecar: FetchResult;
  tui: FetchResult;
  lock: BinaryLock;
}

/**
 * Fetch + verify BOTH binaries. Sequential on purpose: the sidecar is the large
 * download and a failure there should not race a half-finished TUI download.
 *
 * The sidecar lands in the daemon's own cache dir so the daemon — which owns
 * spawning it — finds it already verified instead of downloading it again.
 */
export async function fetchAll(opts: FetchAllOptions = {}): Promise<FetchAllResult> {
  const lock = opts.lock ?? loadLock(opts.lockPath ?? defaultLockPath());
  const platformKey = opts.platformKey ?? currentPlatformKey();
  const common = { ...opts, lock, platformKey };
  const sidecar = await fetchBinary({
    ...common,
    component: "sidecar",
    outDir: opts.sidecarDir ?? daemonSidecarCacheDir(),
  });
  const tui = await fetchBinary({
    ...common,
    component: "tui",
    outDir: opts.tuiDir ?? defaultCacheDir(lock.agentVersion),
  });
  return { sidecar, tui, lock };
}

/** Sync existence check, for the lifecycle layer. */
export function binaryExists(binaryPath: string): boolean {
  return fs.existsSync(binaryPath);
}
