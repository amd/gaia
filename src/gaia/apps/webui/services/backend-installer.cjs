// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * backend-installer.cjs — Shared GAIA Python backend bootstrap logic.
 *
 * Single source of truth for the GAIA backend the app runs. A packaged app
 * runs the frozen `gaia` it ships (<resources>/core) and installs nothing; run
 * from source, it installs `~/.gaia/venv` with `amd-gaia[ui]==<pinned-version>`.
 * Called from both:
 *
 *   - `bin/gaia-ui.cjs`  (the npm CLI entry point)
 *   - `main.cjs`         (the Electron app, on first-run bootstrap)
 *
 * Pure CommonJS with no Electron imports so it can run in both contexts.
 *
 * Exports:
 *   - ensureUv()                     → Promise<void>
 *   - installBackend(opts)           → Promise<void>
 *   - ensureBackend(opts)            → Promise<string>  (returns gaia bin path)
 *   - isBackendCurrent()             → boolean  (nothing to install or refresh)
 *   - stopRunningDaemon(opts)        → Promise<void>  (before its files are replaced)
 *   - getInstalledVersion(gaiaBin)   → string | null
 *   - findGaiaBin()                  → string | null
 *   - getState() / setState()        → state machine helpers
 *   - getLogPath() / getStatePath()  → path helpers
 *   - readLogTail(maxLines)          → string[]
 *   - runPreChecks(opts)             → Promise<PreCheckResult>
 *   - STATES                         → state name constants
 *
 * Progress callbacks are invoked as `onProgress(stage, percent, message)` —
 * the module never touches Electron APIs, so the caller (main.cjs) is
 * responsible for rendering the progress UI.
 */

"use strict";

const { spawn, spawnSync, execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const https = require("https");
const http = require("http");
const tls = require("tls");

// ── Constants ────────────────────────────────────────────────────────────────

const IS_WINDOWS = process.platform === "win32";
const GAIA_HOME = path.join(os.homedir(), ".gaia");
const GAIA_VENV = path.join(GAIA_HOME, "venv");
const GAIA_VENV_DISPLAY = "~/.gaia/venv";
const GAIA_BIN = IS_WINDOWS
  ? path.join(GAIA_VENV, "Scripts", "gaia.exe")
  : path.join(GAIA_VENV, "bin", "gaia");
const GAIA_PYTHON_BIN = IS_WINDOWS
  ? path.join(GAIA_VENV, "Scripts", "python.exe")
  : path.join(GAIA_VENV, "bin", "python");

// The frozen `gaia` a packaged app ships (installer/core/freeze.py). A packaged
// app runs only this one; the venv below is for running the app from source.
const BUNDLED_GAIA_BIN = process.resourcesPath
  ? path.join(process.resourcesPath, "core", IS_WINDOWS ? "gaia.exe" : "gaia")
  : null;
// Not ~/.gaia/core: install.sh unpacks its own gaia there.
const APPIMAGE_CORE_ROOT = path.join(GAIA_HOME, "app-core");
// Written last, so a copy a quit cut short is redone.
const APPIMAGE_CORE_DONE = ".copied";

const STATE_FILE = path.join(GAIA_HOME, "electron-install-state.json");
const LOG_FILE = path.join(GAIA_HOME, "electron-install.log");
// hub/installer.py writes this once the flagship is installed; first-run setup installs it.
const FLAGSHIP_SENTINEL = path.join(GAIA_HOME, "agents", "gaia", ".installed");
/** `gaia init --agent-only` kept the installed agent: the hub couldn't be reached. */
const AGENT_KEPT_EXIT = 5;

// Room for what this installer writes: Python 3.12, uv's wheel cache and the
// `amd-gaia[ui]` venv (0.3 GB on macOS arm64 for 0.25.1rc5, under 1 GB in
// all). Models are not installed here; first-run setup checks the disk for
// the download the user picks.
const MIN_DISK_SPACE_BYTES = 2 * 1024 * 1024 * 1024; // 2 GB
const NETWORK_CHECK_HOSTS = Object.freeze([
  "https://pypi.org/simple/",
  "https://astral.sh",
]);
const NETWORK_CHECK_TIMEOUT_MS = 5000;

// Pip-install resilience: the install stage fetches heavy transitive deps from
// PyPI, so retry a transient network failure a few times before giving up.
const INSTALL_MAX_ATTEMPTS = 3;
const INSTALL_RETRY_BACKOFF_MS = 3000;

const STATES = Object.freeze({
  IDLE: "idle",
  INSTALLING: "installing",
  FAILED: "failed",
  PARTIAL: "partial",
  READY: "ready",
});

const STAGES = Object.freeze({
  PRE_CHECKS: "pre-checks",
  ENSURE_UV: "ensure-uv",
  CREATE_VENV: "create-venv",
  INSTALL_PACKAGE: "install-package",
  VERIFY: "verify",
  // After the package, and outside the weighted bar: it has no percent to report.
  AGENT_REFRESH: "agent-refresh",
});

// Why an install failed, for what the app tells the user to do about it.
const FAILURE_REASONS = Object.freeze({
  DISK: "disk",
  NETWORK: "network",
  TLS: "tls",
  LOCKED: "locked",
  DAEMON: "daemon",
});

// `gaia daemon stop` can take about 90 s: up to 45 s for a graceful exit, then
// terminating the daemon and its agents, then up to 20 s for GAIA's Lemonade Server.
const DAEMON_STOP_TIMEOUT_MS = 120 * 1000;
/** `gaia daemon stop` exit code: the daemon stopped, GAIA's Lemonade Server did not. */
const DAEMON_STOP_LEMONADE_EXIT = 3;
const DAEMON_INSTANCE_FILE = path.join(GAIA_HOME, "host", "instance.json");

// Stage an older installer recorded while it ran `gaia init`; the package was
// already in place by then.
const LEGACY_SETUP_STAGE = "gaia-init";

// Weight each stage contributes to the overall 0-100 progress.
// Sum must equal 100.
const STAGE_WEIGHTS = {
  [STAGES.PRE_CHECKS]: 2,
  [STAGES.ENSURE_UV]: 8,
  [STAGES.CREATE_VENV]: 15,
  [STAGES.INSTALL_PACKAGE]: 73,
  [STAGES.VERIFY]: 2,
};

const STAGE_ORDER = [
  STAGES.PRE_CHECKS,
  STAGES.ENSURE_UV,
  STAGES.CREATE_VENV,
  STAGES.INSTALL_PACKAGE,
  STAGES.VERIFY,
];

// ── Logging ──────────────────────────────────────────────────────────────────

let logStream = null;

/**
 * Log rotation is a session-level concern: we want a fresh log on the first
 * `ensureBackend` call of a given process, but NOT on subsequent retries
 * within the same session, because the original failure log is what the
 * user needs to attach to a bug report after clicking Retry. Flipping this
 * to `true` is a one-way operation; subsequent `openLog({ truncate: true })`
 * calls turn into plain appends.
 */
let logRotatedThisSession = false;

function ensureGaiaHome() {
  try {
    if (!fs.existsSync(GAIA_HOME)) {
      fs.mkdirSync(GAIA_HOME, { recursive: true });
    }
  } catch (err) {
    // Non-fatal; log to console only.
    // We will still try to proceed — callers can fail more loudly.
    // eslint-disable-next-line no-console
    console.error(`[backend-installer] Could not create ${GAIA_HOME}:`, err.message);
  }
}

/**
 * Open the log file for append. When `truncate` is true (i.e. on a fresh
 * install attempt), the existing log is rotated to `${LOG_FILE}.prev` rather
 * than deleted, so the user can still attach the previous attempt to a bug
 * report after clicking Retry. Only the most recent prior attempt is kept.
 */
function openLog({ truncate = false } = {}) {
  ensureGaiaHome();
  try {
    if (logStream) {
      try {
        logStream.end();
      } catch {
        // ignore
      }
      logStream = null;
    }
    // Honor `truncate` only once per process. Multiple retries within the
    // same session (user clicks "Retry" twice) must NOT destroy the
    // original failure log — that's the log the user needs to share.
    const shouldRotate = truncate && !logRotatedThisSession;
    if (truncate && logRotatedThisSession) {
      // no-op, but make it visible in the new log that we intentionally
      // kept the previous attempt's data.
      // eslint-disable-next-line no-console
      console.log(
        "[backend-installer] openLog: retry within same session — appending (no rotation)"
      );
    }
    if (shouldRotate) {
      // Rotate: move the existing log aside (overwriting any older .prev)
      // before opening the new log. This preserves the previous attempt
      // for bug reports while keeping disk usage bounded to two log files.
      try {
        if (fs.existsSync(LOG_FILE)) {
          const prevLog = `${LOG_FILE}.prev`;
          // Use renameSync (atomic on POSIX, near-atomic on Windows)
          try {
            if (fs.existsSync(prevLog)) {
              fs.unlinkSync(prevLog);
            }
            fs.renameSync(LOG_FILE, prevLog);
          } catch (rotateErr) {
            // If rotation fails (e.g. permissions), fall back to truncation
            // so we don't block the install on log housekeeping.
            // eslint-disable-next-line no-console
            console.warn(
              `[backend-installer] Could not rotate log to .prev:`,
              rotateErr.message
            );
          }
        }
      } catch {
        // ignore — rotation is best-effort
      }
      // Mark the session as rotated so future retries append instead of
      // rotating again (preserving the original failure log for bug reports).
      logRotatedThisSession = true;
    }
    logStream = fs.createWriteStream(LOG_FILE, {
      flags: "a",  // always append now (rotation handled above)
    });
    log(`──── backend-installer opened (${new Date().toISOString()}) ────`);
    log(`platform=${process.platform} arch=${process.arch} node=${process.version}`);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error(`[backend-installer] Could not open log ${LOG_FILE}:`, err.message);
    logStream = null;
  }
}

function closeLog() {
  if (logStream) {
    try {
      logStream.end();
    } catch {
      // ignore
    }
    logStream = null;
  }
}

/**
 * Log a line to both the log file and stdout.
 * Accepts the same args as console.log.
 */
function log(...args) {
  const line = args
    .map((a) => (typeof a === "string" ? a : JSON.stringify(a)))
    .join(" ");
  const timestamped = `[${new Date().toISOString()}] ${line}`;
  // eslint-disable-next-line no-console
  console.log(line);
  if (logStream) {
    try {
      logStream.write(timestamped + "\n");
    } catch {
      // ignore
    }
  }
}

/**
 * Log an error line to both the log file and stderr.
 */
function logError(...args) {
  const line = args
    .map((a) => (typeof a === "string" ? a : (a && a.stack) || JSON.stringify(a)))
    .join(" ");
  const timestamped = `[${new Date().toISOString()}] ERROR ${line}`;
  // eslint-disable-next-line no-console
  console.error(line);
  if (logStream) {
    try {
      logStream.write(timestamped + "\n");
    } catch {
      // ignore
    }
  }
}

function getLogPath() {
  return LOG_FILE;
}

function getStatePath() {
  return STATE_FILE;
}

/** The end of the install log, or nothing when no install has written one. */
function readLogTail(maxLines = 40) {
  try {
    return fs.readFileSync(LOG_FILE, "utf8").split(/\r?\n/).filter(Boolean).slice(-maxLines);
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

// ── State machine ────────────────────────────────────────────────────────────

/**
 * Read the persisted install state. Returns `null` if no state file exists
 * or the file is unreadable / corrupt (treated as "idle").
 */
function getState() {
  try {
    if (!fs.existsSync(STATE_FILE)) return null;
    const raw = fs.readFileSync(STATE_FILE, "utf8");
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || !parsed.state) return null;
    return parsed;
  } catch (err) {
    logError(`Could not read install state: ${err.message}`);
    return null;
  }
}

/**
 * Persist the install state to disk. Non-fatal on failure.
 */
function setState(state, extra = {}) {
  ensureGaiaHome();
  const payload = {
    state,
    stage: extra.stage || null,
    message: extra.message || null,
    version: extra.version || null,
    updatedAt: new Date().toISOString(),
    ...extra,
  };
  try {
    fs.writeFileSync(STATE_FILE, JSON.stringify(payload, null, 2), "utf8");
    log(`state: ${state}${extra.stage ? ` (${extra.stage})` : ""}`);
  } catch (err) {
    logError(`Could not write install state: ${err.message}`);
  }
}

function clearState() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      fs.unlinkSync(STATE_FILE);
    }
  } catch (err) {
    logError(`Could not clear install state: ${err.message}`);
  }
}

// ── Progress helpers ─────────────────────────────────────────────────────────

/**
 * Compute overall 0-100 progress given the current stage and within-stage
 * percent (0-100).
 */
function computeOverallPercent(stage, withinStagePercent) {
  const idx = STAGE_ORDER.indexOf(stage);
  if (idx === -1) return 0;
  let base = 0;
  for (let i = 0; i < idx; i++) {
    base += STAGE_WEIGHTS[STAGE_ORDER[i]] || 0;
  }
  const stageWeight = STAGE_WEIGHTS[stage] || 0;
  const within = Math.max(0, Math.min(100, withinStagePercent || 0));
  return Math.max(0, Math.min(100, Math.round(base + (stageWeight * within) / 100)));
}

/**
 * Wrap a caller-provided `onProgress` callback so it converts stage-local
 * progress into overall 0-100 progress.
 */
function makeProgressReporter(onProgress) {
  const safe = typeof onProgress === "function" ? onProgress : () => {};
  return function report(stage, withinStagePercent, message) {
    const percent =
      withinStagePercent === null ? null : computeOverallPercent(stage, withinStagePercent);
    try {
      safe(stage, percent, message || "");
    } catch (err) {
      logError(`onProgress callback threw: ${err.message}`);
    }
  };
}

// ── Command helpers ──────────────────────────────────────────────────────────

/**
 * Check if a command exists on PATH. Throws when the lookup itself can't run.
 */
function commandExists(cmd) {
  // `command -v` is a shell built-in, so it works where `which` isn't installed; the name is $1, never script text.
  const [file, args] = IS_WINDOWS
    ? ["where", [cmd]]
    : ["sh", ["-c", 'command -v "$1"', "sh", cmd]];
  const result = spawnSync(file, args, { stdio: "ignore", shell: false, windowsHide: true });
  if (result.error || result.status === null) {
    const reason = result.error ? result.error.message : `killed by ${result.signal}`;
    throw new InstallError(`Could not check whether "${cmd}" is on PATH: running ${file} failed (${reason}).`, {
      code: result.error ? result.error.code : null,
      suggestion: IS_WINDOWS
        ? "Make sure C:\\Windows\\System32 is on PATH, then restart GAIA."
        : "Make sure /bin/sh exists and /bin is on PATH, then restart GAIA.",
    });
  }
  return result.status === 0;
}

/** The `gaia` this packaged app ships, or null when it ships none. */
function shippedGaiaBin() {
  return BUNDLED_GAIA_BIN && fs.existsSync(BUNDLED_GAIA_BIN) ? BUNDLED_GAIA_BIN : null;
}

/** Where an AppImage keeps its copy of the shipped core for this version. */
function appImageCoreDir() {
  return path.join(APPIMAGE_CORE_ROOT, toPep440(appVersion()));
}

/**
 * The shipped `gaia` as the app runs it, or null until it can run. An
 * AppImage's files vanish when the app quits, and the daemon outlives the app,
 * so an AppImage runs a copy under ~/.gaia once ensureBundledCore has made it.
 */
function bundledGaiaBin() {
  const shipped = shippedGaiaBin();
  if (!shipped || !process.env.APPIMAGE) return shipped;
  const dir = appImageCoreDir();
  return fs.existsSync(path.join(dir, APPIMAGE_CORE_DONE)) ? path.join(dir, path.basename(shipped)) : null;
}

/** bundledGaiaBin, after copying an AppImage's core out of its mount. */
async function ensureBundledCore() {
  const shipped = shippedGaiaBin();
  if (!shipped || !process.env.APPIMAGE) return shipped;
  const ready = bundledGaiaBin();
  if (ready) return ready;
  const dir = appImageCoreDir();
  log(`Copying the backend out of the AppImage to ${dir}`);
  fs.rmSync(dir, { recursive: true, force: true });
  await fs.promises.cp(path.dirname(shipped), dir, { recursive: true, verbatimSymlinks: true });
  fs.writeFileSync(path.join(dir, APPIMAGE_CORE_DONE), appVersion());
  const copy = bundledGaiaBin();
  const older = listDir(APPIMAGE_CORE_ROOT).filter((name) => name !== path.basename(dir));
  if (older.length) {
    // A daemon from an older copy reads its files as it runs, so it stops first.
    await stopRunningDaemon({ gaiaBin: copy });
    for (const name of older) {
      fs.rmSync(path.join(APPIMAGE_CORE_ROOT, name), { recursive: true, force: true });
    }
  }
  return copy;
}

/**
 * Find the gaia binary. A packaged app runs only the one it ships; from
 * source, the managed venv, else PATH.
 */
function findGaiaBin() {
  if (shippedGaiaBin()) {
    return bundledGaiaBin();
  }
  if (fs.existsSync(GAIA_BIN)) {
    return GAIA_BIN;
  }
  if (commandExists("gaia")) {
    return "gaia";
  }
  return null;
}

/**
 * Run a child process and stream output to the log file in real time.
 * Returns a Promise that resolves with { code, stdout, stderr }.
 *
 * `onLine(line)` sees each whole stdout line as it arrives; a line it returns
 * true for is left out of the log.
 *
 * When `signal` aborts, the child and everything it started are stopped and
 * the Promise rejects with InstallCancelled once they are gone.
 */
function runCommand(cmd, args, { env, stageLabel, onLine, signal, timeoutMs } = {}) {
  if (signal && signal.aborted) return Promise.reject(new InstallCancelled());
  return new Promise((resolve, reject) => {
    log(`$ ${cmd} ${args.join(" ")}`);
    let proc;
    try {
      proc = spawn(cmd, args, {
        cwd: os.homedir(),  // Electron's cwd is "/" on macOS when launched from Finder
        env: env || process.env,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
        shell: false,
      });
    } catch (err) {
      logError(`Failed to spawn ${cmd}: ${err.message}`);
      resolve({ code: -1, stdout: "", stderr: String(err.message || err), error: err });
      return;
    }

    let stopping = false;
    const onAbort = () => {
      stopping = true;
      stopTimer();
      log(`Stopping ${cmd}: GAIA is quitting`);
      require("./process-tree.cjs")
        .killProcessTree(proc, { label: stageLabel || cmd, log, logError })
        .then(() => reject(new InstallCancelled()), reject);
    };
    if (signal) signal.addEventListener("abort", onAbort, { once: true });
    const finish = (result) => {
      if (signal) signal.removeEventListener("abort", onAbort);
      if (!stopping) resolve(result);
    };

    let stdout = "";
    let stderr = "";
    let partial = "";
    const logLine = (line) => {
      if (line) log(`  ${stageLabel ? `[${stageLabel}] ` : ""}${line}`);
    };
    // An in-place progress bar redraws with \r, so each redraw is its own segment.
    const takeLine = (line) => {
      let quiet = true;
      for (const segment of line.split("\r")) {
        if (!segment) continue;
        try {
          quiet = onLine(segment) === true && quiet;
        } catch (err) {
          logError(`onLine callback threw: ${err.message}`);
          quiet = false;
        }
      }
      if (!quiet) logLine(line);
    };

    proc.stdout.on("data", (data) => {
      const chunk = data.toString();
      stdout += chunk;
      if (!onLine) {
        chunk.split(/\r?\n/).forEach(logLine);
        return;
      }
      const lines = (partial + chunk).split(/\r?\n/);
      partial = lines.pop();
      lines.forEach(takeLine);
    });
    if (onLine) {
      proc.stdout.on("end", () => {
        if (partial) takeLine(partial);
        partial = "";
      });
    }

    proc.stderr.on("data", (data) => {
      const chunk = data.toString();
      stderr += chunk;
      chunk.split(/\r?\n/).forEach((line) => {
        if (line) log(`  ${stageLabel ? `[${stageLabel}] ` : ""}${line}`);
      });
    });

    // From `timers`, not the globals: the installer tests run this file in a bare vm context.
    let timer = null;
    if (timeoutMs) {
      timer = require("timers").setTimeout(() => {
        logError(`${cmd} ${args.join(" ")} did not finish within ${Math.round(timeoutMs / 1000)}s — ending it`);
        killTree(proc);
        finish({ code: null, stdout, stderr, timedOut: true });
      }, timeoutMs);
    }
    const stopTimer = () => {
      if (timer) require("timers").clearTimeout(timer);
    };

    proc.on("error", (err) => {
      stopTimer();
      logError(`${cmd} error: ${err.message}`);
      finish({ code: -1, stdout, stderr, error: err });
    });

    proc.on("exit", (code) => {
      stopTimer();
      log(`  exit code: ${code}`);
      finish({ code, stdout, stderr });
    });
  });
}

/** End `proc` and what it started: a Windows venv launcher runs Python as a child. */
function killTree(proc) {
  if (IS_WINDOWS) {
    const r = spawnSync("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
      windowsHide: true,
      timeout: 30 * 1000,
    });
    if (r.error || r.status !== 0) {
      logError(`taskkill /PID ${proc.pid} /T /F failed: ${r.error ? r.error.message : `exit ${r.status}`}`);
    }
    return;
  }
  try {
    proc.kill("SIGKILL");
  } catch (err) {
    logError(`Could not end pid ${proc.pid}: ${err.message}`);
  }
}

/** The pid of the GAIA daemon ~/.gaia/host/instance.json records, when that process is alive. */
function liveDaemonPid() {
  let pid;
  try {
    pid = Number(JSON.parse(fs.readFileSync(DAEMON_INSTANCE_FILE, "utf8")).pid);
  } catch (err) {
    if (err.code !== "ENOENT") log(`Could not read ${DAEMON_INSTANCE_FILE}: ${err.message}`);
    return null;
  }
  if (!Number.isInteger(pid) || pid <= 0) return null;
  try {
    process.kill(pid, 0);
    return pid;
  } catch (err) {
    // EPERM: the process exists but belongs to someone else.
    return err.code === "EPERM" ? pid : null;
  }
}

/**
 * Stop the GAIA daemon (and the agents it runs) that the existing venv may be
 * running, so the install doesn't trip over its open files. Throws an
 * InstallError naming the daemon only when one is still alive afterwards: a
 * stop that fails with no daemon running (a broken venv, say) must not block
 * the reinstall that repairs it.
 */
async function stopRunningDaemon({ gaiaBin = GAIA_BIN, timeoutMs = DAEMON_STOP_TIMEOUT_MS, signal } = {}) {
  if (!fs.existsSync(gaiaBin) || (gaiaBin === GAIA_BIN && !fs.existsSync(GAIA_PYTHON_BIN))) return;
  const result = await runCommand(gaiaBin, ["daemon", "stop"], {
    stageLabel: "daemon",
    timeoutMs,
    signal,
    // gaia prints emoji, which a Windows pipe's code page can't encode.
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  if (result.code === 0) return;
  const output = `${result.stdout || ""}\n${result.stderr || ""}`;
  if (result.code === 2 && /invalid choice: '?daemon'?/.test(output)) {
    log("The installed GAIA predates the daemon — nothing to stop");
    return;
  }
  const pid = liveDaemonPid();
  if (pid === null) {
    if (result.code === DAEMON_STOP_LEMONADE_EXIT) {
      log(
        "The GAIA daemon stopped, but GAIA's Lemonade Server did not (see the lines above). " +
          "It lives outside the backend's files, so the install goes on."
      );
    } else {
      log(
        `gaia daemon stop ${result.timedOut ? "timed out" : `exited ${result.code}`}, ` +
          "but no GAIA daemon is running — continuing"
      );
    }
    return;
  }
  const why = result.timedOut
    ? `did not stop within ${Math.round(timeoutMs / 1000)} seconds`
    : `could not be stopped (gaia daemon stop exit ${result.code})`;
  log(`To stop it by hand: ${gaiaBin} daemon stop`);
  throw new InstallError(
    `GAIA's background service (the GAIA daemon, pid ${pid}) ${why}, so its files can't be updated.`,
    {
      stage: STAGES.CREATE_VENV,
      code: result.code,
      reason: FAILURE_REASONS.DAEMON,
      suggestion: `It has no window to close. Restart your PC, then try again. The log at ${LOG_FILE} has the details.`,
    }
  );
}

/**
 * Detect the Windows "file in use" signature in command output. An upgrade's
 * `uv pip install --refresh` can't replace gaia.exe while a previous GAIA
 * process still holds it open — pip reports os error 32 (issue #1388).
 */
function isFileLockedError(output) {
  if (!output) return false;
  return /os error 32/i.test(output) || /being used by another process/i.test(output);
}

/** The files uv could not replace, as absolute paths, in the order it named them. */
function lockedFiles(output) {
  const files = [];
  for (const m of String(output || "").matchAll(/failed to \w+ (?:file|directory) `([^`]+)`/gi)) {
    const file = path.resolve(m[1]);
    if (!files.includes(file)) files.push(file);
  }
  return files;
}

const MOVED_ASIDE_SUFFIX = /\.old-\d+$/i;

function sameFile(a, b) {
  return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
}

/**
 * Rename each locked launcher in the venv's Scripts folder to <name>.old-<ts>
 * so uv can write a fresh one (#6570). A launcher something is running can't
 * be renamed; it is left alone. Returns [{ file, aside }] for those moved.
 */
function moveLockedAside(files) {
  const scripts = path.join(GAIA_VENV, "Scripts");
  const stamp = Date.now();
  const moved = [];
  for (const file of files) {
    if (!/\.exe$/i.test(file) || !sameFile(path.dirname(file), scripts)) continue;
    const aside = `${file}.old-${stamp}`;
    try {
      fs.renameSync(file, aside);
      moved.push({ file, aside });
      log(`Moved locked ${file} aside to ${path.basename(aside)}`);
    } catch (err) {
      log(`Could not move locked ${file} aside (${err.code || err.message}); it stays where it is`);
    }
  }
  return moved;
}

/** Put back every moved launcher uv did not replace, so a failed upgrade leaves the venv whole. */
function putBackUnreplaced(moved) {
  for (const { file, aside } of moved) {
    if (fs.existsSync(file)) {
      log(`${path.basename(file)} was reinstalled; ${path.basename(aside)} is deleted on a later install`);
      continue;
    }
    try {
      fs.renameSync(aside, file);
    } catch (err) {
      throw new Error(`Could not put ${aside} back at ${file}: ${err.message}`);
    }
    log(`Put ${path.basename(file)} back from ${path.basename(aside)}`);
  }
}

/**
 * Tidy what an earlier upgrade moved aside. A .old file whose original was
 * reinstalled is deleted unless still in use; one whose original is missing
 * is put back, newest first.
 */
function removeMovedLaunchers() {
  const dir = path.join(GAIA_VENV, "Scripts");
  const stampOf = (name) => Number(/\.old-(\d+)$/i.exec(name)[1]);
  const leftovers = listDir(dir)
    .filter((name) => MOVED_ASIDE_SUFFIX.test(name))
    .sort((a, b) => stampOf(b) - stampOf(a));
  for (const name of leftovers) {
    const aside = path.join(dir, name);
    const file = aside.replace(MOVED_ASIDE_SUFFIX, "");
    if (!fs.existsSync(file)) {
      fs.renameSync(aside, file);
      log(`Put ${path.basename(file)} back from ${name}: no newer copy was installed`);
      continue;
    }
    try {
      fs.unlinkSync(aside);
      log(`Removed ${aside}, moved aside by an earlier upgrade`);
    } catch (err) {
      // Windows refuses to delete a program that is running (EPERM) or open (EBUSY).
      if (err.code !== "EBUSY" && err.code !== "EPERM") throw err;
      log(`Left ${aside} in place: it is still in use (${err.code})`);
    }
  }
}

/**
 * Detect a transient network failure in `uv pip install` output so the
 * install stage can retry instead of failing the whole bootstrap. The
 * install stage downloads transitive deps (numpy, faiss, pypdfium2, …) from
 * PyPI; a single mid-stream hiccup ("stream closed because of a broken pipe")
 * otherwise fails the entire backend install — and, in the release pipeline,
 * the whole AppImage smoke test that gates publishing.
 *
 * Matches only network-shaped failures — NOT dependency-resolution errors
 * ("No solution found") or disk-full, which retrying cannot fix.
 */
function isTransientNetworkError(output) {
  if (!output) return false;
  return (
    /broken pipe/i.test(output) ||
    /stream closed/i.test(output) ||
    /failed to fetch/i.test(output) ||
    /error sending request/i.test(output) ||
    /connection (?:error|reset|closed|refused|aborted)/i.test(output) ||
    /could not connect/i.test(output) ||
    /(?:request|operation|connection)?\s*tim(?:ed\s*out|eout)/i.test(output) ||
    /temporary failure in name resolution/i.test(output) ||
    /(?:could not resolve|failed to lookup|dns error)/i.test(output) ||
    /network is unreachable/i.test(output)
  );
}

/**
 * Detect a TLS trust failure in `uv pip install` output. Behind a corporate
 * MITM proxy, PyPI is presented with a certificate signed by a custom root CA
 * that uv's bundled webpki roots don't include, so uv reports
 * "invalid peer certificate: UnknownIssuer" (issue #1693). It surfaces wrapped
 * in "Failed to fetch" / "error sending request", so isTransientNetworkError
 * also matches it — the install loop MUST check this first and retry with
 * --native-tls (the OS trust store, where IT installs the corporate CA) rather
 * than burning the transient retries on the same bundled roots.
 */
function isTlsCertError(output) {
  if (!output) return false;
  return (
    /invalid peer certificate/i.test(output) ||
    /unknownissuer/i.test(output) ||
    /certificate verify failed/i.test(output) ||
    /unable to get local issuer certificate/i.test(output) ||
    /self[- ]signed certificate/i.test(output)
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Pre-checks ───────────────────────────────────────────────────────────────

/**
 * Check disk space at `~/.gaia/`'s parent.
 * Returns { ok, freeBytes, requiredBytes, message? }.
 */
function checkDiskSpace() {
  const parent = path.dirname(GAIA_HOME);
  try {
    // Node 18.15+ has fs.statfsSync on all platforms.
    if (typeof fs.statfsSync === "function") {
      const stat = fs.statfsSync(parent);
      // `bavail` is blocks available to unprivileged users; `bsize` is block size.
      const free = BigInt(stat.bavail) * BigInt(stat.bsize);
      const freeBytes = Number(free);
      return {
        ok: freeBytes >= MIN_DISK_SPACE_BYTES,
        freeBytes,
        requiredBytes: MIN_DISK_SPACE_BYTES,
      };
    }
  } catch (err) {
    logError(`statfsSync failed: ${err.message}`);
  }

  // Fallback: platform-specific tools. Non-fatal if unavailable.
  try {
    if (IS_WINDOWS) {
      // The drive letter reaches PowerShell through the environment, not the script text.
      const drive = path.parse(parent).root.replace(/[:\\]+$/, "");
      const out = execFileSync(
        "powershell",
        ["-NoProfile", "-Command", "(Get-PSDrive -Name $env:GAIA_DISK_DRIVE).Free"],
        {
          encoding: "utf8",
          timeout: 5000,
          windowsHide: true,
          env: { ...process.env, GAIA_DISK_DRIVE: drive },
        }
      ).trim();
      const freeBytes = parseInt(out, 10);
      if (!Number.isNaN(freeBytes)) {
        return {
          ok: freeBytes >= MIN_DISK_SPACE_BYTES,
          freeBytes,
          requiredBytes: MIN_DISK_SPACE_BYTES,
        };
      }
    } else {
      // `df -k <parent>` — second line, 4th column is available 1K blocks.
      const out = execFileSync("df", ["-k", parent], { encoding: "utf8", timeout: 5000 });
      const lines = out.trim().split("\n");
      if (lines.length >= 2) {
        const cols = lines[lines.length - 1].trim().split(/\s+/);
        // `df` can have 6 or 9 columns depending on platform; available is
        // usually the 4th field (Linux) or also the 4th field on macOS.
        const availKb = parseInt(cols[3], 10);
        if (!Number.isNaN(availKb)) {
          const freeBytes = availKb * 1024;
          return {
            ok: freeBytes >= MIN_DISK_SPACE_BYTES,
            freeBytes,
            requiredBytes: MIN_DISK_SPACE_BYTES,
          };
        }
      }
    }
  } catch (err) {
    logError(`Fallback disk-space check failed: ${err.message}`);
  }

  // Could not determine — be optimistic but record a warning.
  log("Warning: could not determine free disk space; proceeding anyway");
  return {
    ok: true,
    freeBytes: null,
    requiredBytes: MIN_DISK_SPACE_BYTES,
    message: "Free disk space could not be determined",
  };
}

/**
 * Read the OS trust store. Node 22+ exposes `tls.getCACertificates("system")`;
 * older Node lacks it, so this returns `[]` there (callers fall back to
 * NODE_EXTRA_CA_CERTS / the bundled Mozilla set).
 *
 * Why this exists: behind a corporate TLS-inspection proxy, the proxy's root
 * CA is installed in the OS trust store but NOT in Node's bundled Mozilla set,
 * so every HTTPS handshake fails with UNABLE_TO_GET_ISSUER_CERT_LOCALLY and the
 * machine looks "offline" when it is online (issue #1572).
 */
function _systemCaCertificates() {
  try {
    if (typeof tls.getCACertificates === "function") {
      return tls.getCACertificates("system") || [];
    }
  } catch (err) {
    log(`Could not read system CA store: ${err.message}`);
  }
  return [];
}

/** Read a pinned corporate root CA from NODE_EXTRA_CA_CERTS (any Node version). */
function _extraCaCertificates() {
  const file = process.env.NODE_EXTRA_CA_CERTS;
  if (!file) return [];
  try {
    return [fs.readFileSync(file, "utf8")];
  } catch (err) {
    // err.message already names the offending path — don't re-log the env value.
    log(`Could not read NODE_EXTRA_CA_CERTS: ${err.message}`);
    return [];
  }
}

/**
 * Build the CA trust bundle for the network probe. Passing `ca` to
 * `https.request` REPLACES Node's default trust store, so when we add system /
 * extra certs we must re-include the bundled Mozilla set. Returns `undefined`
 * when there is nothing extra to add (probe uses Node's default trust store —
 * behaviour unchanged).
 */
function buildCaBundle() {
  const extra = [..._systemCaCertificates(), ..._extraCaCertificates()];
  if (extra.length === 0) return undefined;
  return [...new Set([...tls.rootCertificates, ...extra])];
}

/** First non-empty proxy URL from the standard env vars, or null. */
function proxyForHttps() {
  return (
    process.env.HTTPS_PROXY ||
    process.env.https_proxy ||
    process.env.HTTP_PROXY ||
    process.env.http_proxy ||
    null
  );
}

/**
 * Classify a network error so the caller can tell a trust-store gap (NOT
 * offline) apart from genuine connectivity loss.
 *   - "tls"          → certificate/handshake failure (machine is online)
 *   - "timeout"      → request timed out
 *   - "connectivity" → DNS/connect failure (likely offline)
 */
function classifyNetworkError(err) {
  const code = (err && err.code) || "";
  const message = (err && err.message) || "";
  if (/CERT|SELF_SIGNED|UNABLE_TO_|_SIGNATURE|ALTNAME/.test(code)) {
    return "tls";
  }
  if (code === "ETIMEDOUT" || /timed out/i.test(message)) {
    return "timeout";
  }
  return "connectivity";
}

/** HEAD-probe `target` (a URL) directly, trusting `ca`. */
function _probeDirect(target, ca, finish, setSock) {
  const opts = {
    host: target.hostname,
    port: target.port || 443,
    path: target.pathname || "/",
    method: "HEAD",
    servername: target.hostname,
    headers: { "User-Agent": "gaia-backend-installer/1.0" },
  };
  if (ca) opts.ca = ca;
  try {
    const req = https.request(opts, (res) => {
      res.resume();
      finish({ ok: true, status: res.statusCode });
    });
    setSock(req);
    req.on("error", (err) =>
      finish({ ok: false, kind: classifyNetworkError(err), message: `${target.href}: ${err.message}` })
    );
    req.end();
  } catch (err) {
    finish({ ok: false, kind: classifyNetworkError(err), message: `${target.href}: ${err.message}` });
  }
}

/** HEAD-probe `target` through an HTTP CONNECT proxy, trusting `ca`. */
function _probeViaProxy(target, proxy, ca, finish, setSock) {
  let proxyUrl;
  try {
    proxyUrl = new URL(proxy);
  } catch {
    // HTTPS_PROXY/HTTP_PROXY may embed credentials (http://user:pass@host) —
    // strip the userinfo before the value reaches the install log.
    const redacted = proxy.replace(/^(\w+:\/\/)[^@/]*@/, "$1***@");
    finish({ ok: false, kind: "connectivity", message: `${target.href}: invalid proxy URL "${redacted}"` });
    return;
  }
  const headers = {};
  if (proxyUrl.username) {
    const cred = `${decodeURIComponent(proxyUrl.username)}:${decodeURIComponent(proxyUrl.password)}`;
    headers["Proxy-Authorization"] = "Basic " + Buffer.from(cred).toString("base64");
  }
  const connectReq = http.request({
    host: proxyUrl.hostname,
    port: proxyUrl.port || 80,
    method: "CONNECT",
    path: `${target.hostname}:${target.port || 443}`,
    headers,
  });
  setSock(connectReq);
  connectReq.on("connect", (res, socket) => {
    if (res.statusCode !== 200) {
      try { socket.destroy(); } catch { /* ignore */ }
      finish({ ok: false, kind: "connectivity", message: `${target.href}: proxy CONNECT returned ${res.statusCode}` });
      return;
    }
    setSock(socket);
    const opts = {
      host: target.hostname,
      port: target.port || 443,
      path: target.pathname || "/",
      method: "HEAD",
      socket,
      agent: false,
      servername: target.hostname,
      headers: { "User-Agent": "gaia-backend-installer/1.0" },
    };
    if (ca) opts.ca = ca;
    try {
      const req = https.request(opts, (r) => {
        r.resume();
        finish({ ok: true, status: r.statusCode });
      });
      req.on("error", (err) =>
        finish({ ok: false, kind: classifyNetworkError(err), message: `${target.href}: ${err.message}` })
      );
      req.end();
    } catch (err) {
      finish({ ok: false, kind: classifyNetworkError(err), message: `${target.href}: ${err.message}` });
    }
  });
  connectReq.on("error", (err) =>
    finish({ ok: false, kind: classifyNetworkError(err), message: `${target.href}: ${err.message}` })
  );
  connectReq.end();
}

/**
 * Best-effort reachability probe for a single host. A HEAD that gets any
 * response (even 3xx/4xx) proves connectivity. Trusts the system / pinned CA
 * store (issue #1572) and honors HTTPS_PROXY/HTTP_PROXY. Resolves
 * { ok, status? } or { ok:false, kind, message }.
 */
function _checkOneHost(url) {
  return new Promise((resolve) => {
    let settled = false;
    let sock = null;
    let timer = null;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (sock) {
        try { sock.destroy(); } catch { /* ignore */ }
      }
      resolve(result);
    };
    const setSock = (s) => {
      sock = s;
    };
    timer = setTimeout(() => {
      finish({
        ok: false,
        kind: "timeout",
        message: `${url}: timed out after ${NETWORK_CHECK_TIMEOUT_MS / 1000}s`,
      });
    }, NETWORK_CHECK_TIMEOUT_MS);

    let target;
    try {
      target = new URL(url);
    } catch (err) {
      finish({ ok: false, kind: "connectivity", message: `${url}: ${err.message}` });
      return;
    }
    const ca = buildCaBundle();
    const proxy = proxyForHttps();
    if (proxy) {
      _probeViaProxy(target, proxy, ca, finish, setSock);
    } else {
      _probeDirect(target, ca, finish, setSock);
    }
  });
}

/**
 * Probe each host in ``NETWORK_CHECK_HOSTS`` sequentially. Succeed as soon as
 * ANY host responds (even 3xx/4xx counts — it proves connectivity). On total
 * failure, report the dominant failure `kind` so the caller can distinguish a
 * trust-store gap ("tls" — online but Node doesn't trust the proxy CA) from a
 * genuine outage ("connectivity").
 */
async function checkNetwork() {
  const errors = [];
  const kinds = new Set();
  for (const url of NETWORK_CHECK_HOSTS) {
    const result = await _checkOneHost(url);
    if (result.ok) return { ok: true, status: result.status };
    errors.push(result.message);
    kinds.add(result.kind || "connectivity");
  }
  const allTls = kinds.size > 0 && [...kinds].every((k) => k === "tls");
  return {
    ok: false,
    kind: allTls ? "tls" : kinds.has("connectivity") ? "connectivity" : [...kinds][0] || "connectivity",
    message: `Network check failed for all hosts: ${errors.join("; ")}`,
  };
}

/**
 * Run all pre-checks. Returns a structured result; the caller decides what
 * to do on failure (show a dialog, abort, etc.).
 *
 * Shape:
 *   {
 *     ok: boolean,
 *     disk:   { ok, freeBytes, requiredBytes, message? },
 *     network:{ ok, message? },
 *     previousState: object | null,
 *   }
 */
async function runPreChecks(opts = {}) {
  const report = makeProgressReporter(opts.onProgress);
  report(STAGES.PRE_CHECKS, 0, "Running pre-flight checks");

  log("Running pre-checks...");
  const previousState = getState();
  if (previousState && previousState.state) {
    log(`Found existing state file: ${previousState.state}`);
  }

  report(STAGES.PRE_CHECKS, 25, "Checking disk space");
  const disk = checkDiskSpace();
  log(
    `Disk: ${disk.ok ? "ok" : "insufficient"} (free=${disk.freeBytes}, required=${disk.requiredBytes})`
  );

  report(STAGES.PRE_CHECKS, 60, "Checking network connectivity");
  const network = await checkNetwork();
  log(`Network: ${network.ok ? "ok" : "unreachable"} ${network.message || ""}`);

  report(STAGES.PRE_CHECKS, 100, "Pre-flight checks complete");

  return {
    ok: disk.ok && network.ok,
    disk,
    network,
    previousState,
  };
}

// ── uv install ───────────────────────────────────────────────────────────────

class InstallError extends Error {
  constructor(message, { stage, code, suggestion, reason, lockedFiles, keepState } = {}) {
    super(message);
    this.name = "InstallError";
    this.stage = stage || null;
    this.code = code || null;
    this.suggestion = suggestion || null;
    this.reason = reason || null;
    // The files another program held open, for main to find who holds them.
    this.lockedFiles = lockedFiles || null;
    // Nothing was touched, so the recorded state still holds.
    this.keepState = Boolean(keepState);
  }
}

/** The caller's signal stopped the install: the app is quitting, nothing failed. */
class InstallCancelled extends Error {
  constructor() {
    super("The install stopped because GAIA is quitting; it continues on the next launch.");
    this.name = "InstallCancelled";
  }
}

/** ENOSPC on POSIX (os error 28) and ERROR_DISK_FULL on Windows (os error 112). */
function isDiskFullError(output) {
  return /No space left on device|not enough space on the disk|os error (28|112)\b/i.test(output || "");
}

/**
 * The pre-check found too little space to install. `keep`: a working install
 * is in place and the refusal leaves it, and its recorded state, untouched.
 */
function lowDiskError(disk, { keep, installedVersion }) {
  const mb = (bytes) => Math.round(bytes / (1024 * 1024));
  const requiredMb = mb(disk.requiredBytes);
  const freeMb = disk.freeBytes != null ? mb(disk.freeBytes) : null;
  const need = freeMb != null ? `${requiredMb - freeMb} MB more` : `${requiredMb} MB`;
  const kept = keep ? ` Your installed GAIA${installedVersion ? ` ${installedVersion}` : ""} was not changed.` : "";
  return new InstallError(
    `Not enough free disk space${keep ? " to update GAIA" : ""}. Required: ${requiredMb} MB${
      freeMb != null ? `, available: ${freeMb} MB` : ""
    }.${kept}`,
    {
      stage: STAGES.PRE_CHECKS,
      reason: FAILURE_REASONS.DISK,
      suggestion: `Free at least ${need} at ${path.dirname(GAIA_HOME)}, then try again.`,
      keepState: keep,
    }
  );
}

/** Which FAILURE_REASONS a uv failure's output shows, or null. */
function failureReason(output) {
  if (isDiskFullError(output)) return FAILURE_REASONS.DISK;
  if (isFileLockedError(output)) return FAILURE_REASONS.LOCKED;
  if (isTlsCertError(output)) return FAILURE_REASONS.TLS;
  if (isTransientNetworkError(output)) return FAILURE_REASONS.NETWORK;
  return null;
}

/**
 * Ensure `uv` is available. Only running the app from source gets here: a
 * packaged app ships its backend. Uses the uv on PATH, else installs it from
 * astral.sh.
 *
 * Throws InstallError on failure.
 */
async function ensureUv({ onProgress, signal } = {}) {
  const report = makeProgressReporter(onProgress);
  report(STAGES.ENSURE_UV, 0, "Checking uv (Python package manager)");

  if (commandExists("uv")) {
    log("uv already on PATH (dev) — using system install");
    report(STAGES.ENSURE_UV, 100, "uv is already installed (system)");
    return;
  }

  log("No uv on PATH — installing it from astral.sh");
  let result;
  if (IS_WINDOWS) {
    result = await runCommand(
      "powershell",
      [
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "irm https://astral.sh/uv/install.ps1 | iex",
      ],
      { stageLabel: "uv-install", signal }
    );
  } else {
    result = await runCommand(
      "bash",
      ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
      { stageLabel: "uv-install", signal }
    );
  }

  if (result.code !== 0) {
    throw new InstallError(
      `Could not install uv automatically (exit code ${result.code}).`,
      {
        stage: STAGES.ENSURE_UV,
        code: result.code,
        suggestion: IS_WINDOWS
          ? 'Install uv manually: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
          : "Install uv manually: curl -LsSf https://astral.sh/uv/install.sh | sh",
      }
    );
  }

  if (!commandExists("uv")) {
    const candidates = [
      path.join(os.homedir(), ".local", "bin"),
      path.join(os.homedir(), ".cargo", "bin"),
    ];
    for (const uvDir of candidates) {
      if (process.env.PATH && !process.env.PATH.includes(uvDir)) {
        process.env.PATH = `${uvDir}${path.delimiter}${process.env.PATH}`;
        log(`Added ${uvDir} to PATH for this process`);
      }
    }
  }

  if (!commandExists("uv")) {
    throw new InstallError(
      "uv installed but not found on PATH. A shell restart may be required.",
      {
        stage: STAGES.ENSURE_UV,
        suggestion:
          "Restart your terminal or reboot, then re-launch GAIA. If the problem persists, install uv manually from https://astral.sh/uv",
      }
    );
  }

  report(STAGES.ENSURE_UV, 100, "uv installed");
}

// ── Backend install ──────────────────────────────────────────────────────────

/**
 * Read the pinned backend version from package.json (or a caller override).
 * Returns null when GAIA_LOCAL_WHEEL is set — the caller uses the wheel path
 * directly and skips the PyPI version pin (CI release-build fast-path).
 */
function resolveBackendVersion(opts = {}) {
  if (opts.version) return opts.version;
  // CI override: install from a local wheel instead of a pinned PyPI version.
  // Breaks the circular dependency in release builds where the AppImage smoke
  // test runs before PyPI publish.
  if (process.env.GAIA_LOCAL_WHEEL) return null;
  try {
    // package.json is one directory up from the services/ (or bin/) directory.
    // We look relative to this module's own location.
    const pkgPath = path.join(__dirname, "..", "package.json");
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
    return pkg.version || "latest";
  } catch (err) {
    logError(`Could not read package.json version: ${err.message}`);
    return "latest";
  }
}

/** The version the app ships with, whatever backend this run was asked to install. */
function appVersion() {
  return JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8")).version;
}

/**
 * The PEP 440 spelling of the app's npm version: `0.25.0-rc.1` -> `0.25.0rc1`.
 * pip pins and `gaia --version` use this form; a final version is unchanged.
 */
function toPep440(version) {
  if (!version) return version;
  return version.replace(/^(\d+\.\d+\.\d+)-rc\.(\d+)$/, "$1rc$2");
}

/**
 * Install the GAIA Python backend from scratch.
 *
 * Installs no model and runs no `gaia init`: the Lemonade Server and the models
 * are set up by the app's first-run setup, where the user chooses cloud or
 * local and sees what downloads.
 *
 * opts:
 *   - onProgress(stage, percent, message)
 *   - version: string — override the pinned version
 *
 * Throws InstallError on failure. The state file is updated to reflect
 * the current stage so a subsequent launch can recover.
 */
async function installBackend(opts = {}) {
  const report = makeProgressReporter(opts.onProgress);
  const version = resolveBackendVersion(opts);
  // GAIA_LOCAL_WHEEL: CI-only. When set, install from the given wheel path
  // instead of pulling from PyPI. This breaks the circular dependency in
  // release pipeline smoke tests that run before PyPI publish. The `[ui]`
  // extras marker is preserved so the local install matches the PyPI path
  // (fastapi, uvicorn, python-multipart, httpx, psutil) — otherwise the
  // backend venv comes up missing every UI dep and /api/health never binds.
  const localWheel = process.env.GAIA_LOCAL_WHEEL || null;
  const pipPackage = localWheel
    ? `${localWheel}[ui]`
    : `amd-gaia[ui]==${toPep440(version)}`;

  log("================================================");
  log("  Installing GAIA backend");
  log("================================================");
  log(`Package: ${pipPackage}`);
  log(`Location: ${GAIA_VENV_DISPLAY}`);

  setState(STATES.INSTALLING, { stage: STAGES.ENSURE_UV, version });

  // Stage 1: ensure uv
  await ensureUv({ onProgress: opts.onProgress, isPackaged: opts.isPackaged, signal: opts.signal });

  // Stage 2: create venv
  setState(STATES.INSTALLING, { stage: STAGES.CREATE_VENV, version });
  report(STAGES.CREATE_VENV, 0, "Creating Python 3.12 environment");

  ensureGaiaHome();
  await stopRunningDaemon({ signal: opts.signal });

  // If the venv exists but the python binary is missing, treat as partial.
  const venvLooksValid =
    fs.existsSync(GAIA_VENV) && fs.existsSync(GAIA_PYTHON_BIN);

  if (!venvLooksValid) {
    if (fs.existsSync(GAIA_VENV)) {
      log("Existing venv appears broken — removing and recreating");
      try {
        fs.rmSync(GAIA_VENV, { recursive: true, force: true });
      } catch (err) {
        logError(`Could not remove broken venv: ${err.message}`);
      }
    }

    const venvArgs = ["venv", GAIA_VENV, "--python", "3.12"];
    let venvResult = await runCommand("uv", venvArgs, { stageLabel: "venv", signal: opts.signal });
    // uv downloads Python 3.12 when none is installed; behind a corporate
    // proxy that fails on uv's bundled roots exactly like the pip step below.
    let venvNativeTls = false;
    if (
      venvResult.code !== 0 &&
      isTlsCertError(`${venvResult.stdout || ""}\n${venvResult.stderr || ""}`)
    ) {
      venvNativeTls = true;
      log(
        "TLS trust failure downloading Python — retrying with --native-tls " +
          "to use the OS certificate store"
      );
      report(
        STAGES.CREATE_VENV,
        0,
        "Certificate trust issue — retrying with the system certificate store"
      );
      if (fs.existsSync(GAIA_VENV)) {
        fs.rmSync(GAIA_VENV, { recursive: true, force: true });
      }
      venvResult = await runCommand("uv", [...venvArgs, "--native-tls"], {
        stageLabel: "venv",
        signal: opts.signal,
      });
    }
    if (venvResult.code !== 0) {
      const manual = `uv venv ${GAIA_VENV_DISPLAY} --python 3.12${
        venvNativeTls ? " --native-tls" : ""
      }`;
      throw new InstallError(
        `Failed to create Python environment (uv venv exit ${venvResult.code}).`,
        {
          stage: STAGES.CREATE_VENV,
          code: venvResult.code,
          reason: failureReason(`${venvResult.stdout || ""}\n${venvResult.stderr || ""}`),
          suggestion: `Try creating it manually:\n  ${manual}\nThen restart GAIA.`,
        }
      );
    }
  } else {
    log("Existing venv looks valid — reusing");
  }
  report(STAGES.CREATE_VENV, 100, "Python environment ready");

  // Stage 3: pip install
  setState(STATES.INSTALLING, { stage: STAGES.INSTALL_PACKAGE, version });
  report(STAGES.INSTALL_PACKAGE, 0, `Installing ${pipPackage}`);
  if (IS_WINDOWS) removeMovedLaunchers();

  const pipArgs = [
    "pip",
    "install",
    pipPackage,
    "--refresh",
    // uv skips this by default, leaving the first daemon start to compile GAIA.
    "--compile-bytecode",
    "--python",
    GAIA_PYTHON_BIN,
  ];
  // Retry the install on transient PyPI/network failures. The transitive
  // deps (numpy, faiss, pypdfium2, …) are fetched live from PyPI even
  // when the gaia wheel itself is local, so a single broken-pipe mid-download
  // would otherwise fail the whole bootstrap (and block a release). File-lock
  // failures (Windows os-error-32) get their own single retry after this loop.
  let installResult;
  if (!localWheel && isBackendPrefetched(version)) {
    report(STAGES.INSTALL_PACKAGE, 0, `Installing ${pipPackage} from the downloaded update`);
    const offline = await runCommand(
      "uv",
      ["pip", "install", pipPackage, "--offline", "--compile-bytecode", "--python", GAIA_PYTHON_BIN],
      { stageLabel: "pip", signal: opts.signal }
    );
    fs.rmSync(_prefetchMarker(toPep440(version)), { force: true });
    if (offline.code === 0) {
      installResult = offline;
    } else {
      log(`The prefetched backend is no longer all in uv's cache (${_lastLines(offline)}) — installing from PyPI`);
    }
  }
  const installedOffline = Boolean(installResult);
  let useNativeTls = false;
  let nativeTlsAttempted = false;
  // The TLS branch flips useNativeTls and `continue`s, consuming an iteration.
  // If the signature only surfaces on the final attempt, extend the loop once
  // so the promised --native-tls retry actually runs (issue #1693 review).
  for (
    let attempt = 1;
    !installedOffline && (attempt <= INSTALL_MAX_ATTEMPTS || (useNativeTls && !nativeTlsAttempted));
    attempt++
  ) {
    const attemptArgs = useNativeTls ? [...pipArgs, "--native-tls"] : pipArgs;
    if (useNativeTls) nativeTlsAttempted = true;
    installResult = await runCommand("uv", attemptArgs, { stageLabel: "pip", signal: opts.signal });
    if (installResult.code === 0) break;
    const attemptOutput = `${installResult.stdout || ""}\n${installResult.stderr || ""}`;
    // A corporate proxy's custom root CA isn't in uv's bundled webpki roots, so
    // the fetch fails with UnknownIssuer. This LOOKS transient ("Failed to
    // fetch") but retrying the bundled roots can't fix it — retry once with
    // --native-tls so uv trusts the OS certificate store instead (issue #1693).
    if (!useNativeTls && isTlsCertError(attemptOutput)) {
      useNativeTls = true;
      log(
        "TLS trust failure (corporate proxy CA not in uv's bundled roots) — " +
          "retrying install with --native-tls to use the OS certificate store"
      );
      report(
        STAGES.INSTALL_PACKAGE,
        0,
        "Certificate trust issue — retrying with the system certificate store"
      );
      continue;
    }
    const canRetry =
      attempt < INSTALL_MAX_ATTEMPTS &&
      isTransientNetworkError(attemptOutput) &&
      !isFileLockedError(attemptOutput);
    if (!canRetry) break;
    const backoffMs = INSTALL_RETRY_BACKOFF_MS * attempt;
    log(
      `Transient network error during install (attempt ${attempt}/${INSTALL_MAX_ATTEMPTS}). ` +
        `Retrying in ${Math.round(backoffMs / 1000)}s…`
    );
    report(
      STAGES.INSTALL_PACKAGE,
      0,
      `Network hiccup — retrying install (attempt ${attempt + 1}/${INSTALL_MAX_ATTEMPTS})`
    );
    await sleep(backoffMs);
  }
  // A launcher that is only open, not running, can be renamed out of uv's way (#6570).
  const firstOutput = `${installResult.stdout || ""}\n${installResult.stderr || ""}`;
  if (IS_WINDOWS && installResult.code !== 0 && isFileLockedError(firstOutput)) {
    const moved = moveLockedAside(lockedFiles(firstOutput));
    if (moved.length > 0) {
      report(STAGES.INSTALL_PACKAGE, 0, "Another program has GAIA's files open — retrying the install");
      try {
        installResult = await runCommand("uv", useNativeTls ? [...pipArgs, "--native-tls"] : pipArgs, {
          stageLabel: "pip",
          signal: opts.signal,
        });
      } finally {
        putBackUnreplaced(moved);
      }
    }
  }
  if (installResult.code !== 0) {
    const output = `${installResult.stdout || ""}\n${installResult.stderr || ""}`;
    let suggestion;
    if (isFileLockedError(output)) {
      // pip's own line is already in the log, which the setup screen's details show.
      const files = lockedFiles(output);
      const names = files.map((file) => path.basename(file)).join(", ");
      throw new InstallError(
        `GAIA couldn't replace its own files because another program has them open${names ? ` (${names})` : ""}.`,
        {
          stage: STAGES.INSTALL_PACKAGE,
          code: installResult.code,
          reason: FAILURE_REASONS.LOCKED,
          lockedFiles: files,
          suggestion:
            "Close other GAIA apps and any terminal running gaia, or restart your PC, then try again. " +
            `The log at ${LOG_FILE} names the file.`,
        }
      );
    } else if (isTlsCertError(output)) {
      suggestion =
        "PyPI's certificate isn't trusted on this network — usually a corporate proxy presenting its own root CA. " +
        (nativeTlsAttempted
          ? "GAIA retried with the OS certificate store and it still failed. "
          : "") +
        "Ask IT to install the proxy's root CA in your system's trusted certificate store, then relaunch GAIA. " +
        "See https://amd-gaia.ai/docs/quickstart#cli-install";
    } else if (isTransientNetworkError(output)) {
      // The log file keeps pip's output; the box says what to do about it.
      throw new InstallError("GAIA couldn't reach the internet to finish installing.", {
        stage: STAGES.INSTALL_PACKAGE,
        code: installResult.code,
        suggestion:
          "Check your connection, then choose Retry. If it keeps failing, the log file below says why. Your chats and settings are safe.",
      });
    } else {
      suggestion = `Try installing manually:\n  uv pip install ${pipPackage} --python ${
        IS_WINDOWS ? `${GAIA_VENV_DISPLAY}/Scripts/python.exe` : `${GAIA_VENV_DISPLAY}/bin/python`
      }\nThen restart GAIA. See https://amd-gaia.ai/docs/quickstart#cli-install`;
    }
    // Carry pip's own last words. Without them the only record of a failed
    // install is an exit code, which is not something a user or a CI log can
    // act on.
    const tail = output
      .split("\n")
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .slice(-20)
      .join("\n");
    throw new InstallError(
      `Failed to install ${pipPackage} (pip exit ${installResult.code}).` +
        (tail ? `\npip said:\n${tail}` : ""),
      {
        stage: STAGES.INSTALL_PACKAGE,
        code: installResult.code,
        reason: failureReason(output),
        suggestion,
      }
    );
  }

  if (!fs.existsSync(GAIA_BIN)) {
    throw new InstallError(
      `GAIA binary not found at ${GAIA_VENV_DISPLAY} after install.`,
      {
        stage: STAGES.INSTALL_PACKAGE,
        suggestion: "The package was installed but the gaia executable is missing. Try reinstalling from https://amd-gaia.ai/docs/quickstart",
      }
    );
  }
  report(STAGES.INSTALL_PACKAGE, 100, "GAIA package installed");

  // Stage 4: verify
  setState(STATES.INSTALLING, { stage: STAGES.VERIFY, version });
  report(STAGES.VERIFY, 0, "Verifying installation");

  const verifiedBin = findGaiaBin();
  if (!verifiedBin) {
    throw new InstallError(
      "GAIA backend not found after install verification.",
      {
        stage: STAGES.VERIFY,
        suggestion: "Check the log file for details and try reinstalling.",
      }
    );
  }
  const installedVersion = getInstalledVersion(verifiedBin);
  log(`Verified gaia binary: ${verifiedBin} (version=${installedVersion || "unknown"})`);
  report(STAGES.VERIFY, 100, "Install verified");

  // Ensure a user-accessible shim is created so users who installed via
  // AppImage can run `gaia` from a terminal without manually adding the
  // venv bin directory to their PATH. Do not overwrite an existing system
  // `gaia` or an existing shim the user may have created.
  try {
    // Only create shims on POSIX-like systems (AppImage target).
    if (process.platform !== "win32") {
      const userBin = process.env.XDG_BIN_HOME || path.join(os.homedir(), ".local", "bin");
      const shimPath = path.join(userBin, "gaia");

      // Only create a shim if there's no `gaia` already on PATH and no shim
      // at the target location. This avoids clobbering system packages.
      if (!commandExists("gaia") && !fs.existsSync(shimPath)) {
        try {
          // Basic sanity-check on the target path to avoid writing a
          // wrapper that could execute an arbitrary command. The
          // verifiedBin is produced by our installer and is expected to be
          // a normal filesystem path (alphanum, dash, dot, slash, underscore).
          if (!/^[\w\-./]+$/.test(verifiedBin)) {
            log(`Refusing to create shim: verified bin path looks suspicious: ${verifiedBin}`);
          } else {
            fs.mkdirSync(userBin, { recursive: true });
            const wrapper = `#!/bin/sh\nexec \"${verifiedBin}\" \"$@\"\n`;
            fs.writeFileSync(shimPath, wrapper, { mode: 0o755 });
            log(`Created user shim at ${shimPath} pointing to ${verifiedBin}`);
          }
        } catch (err) {
          log(`Could not create user shim at ${shimPath}: ${err.message}`);
        }
      } else if (fs.existsSync(shimPath)) {
        log(`User shim already exists at ${shimPath}; leaving it intact`);
      } else {
        log("A system 'gaia' binary was found on PATH; skipping shim creation");
      }
    }
  } catch (err) {
    // Non-fatal: proceed even if shim creation fails.
    log(`Shim creation check failed: ${err.message}`);
  }

  setState(STATES.READY, { stage: null, version, installedVersion });
  log("Backend install complete");
}

// ── Agent refresh ────────────────────────────────────────────────────────────

/**
 * Update the flagship to this release, without Lemonade or models. A PC with
 * no flagship is skipped: first-run setup installs it.
 *
 * The stage stays recorded until this succeeds, so an update that failed or was
 * quit part way through refreshes again on the next launch.
 */
async function refreshAgent(gaiaBin, opts = {}) {
  const version = resolveBackendVersion(opts);
  if (!fs.existsSync(FLAGSHIP_SENTINEL)) {
    log("No GAIA agent installed yet — first-run setup installs it");
    // Nothing to refresh: a refresh recorded earlier is done, not pending forever.
    setState(STATES.READY, { version, installedVersion: getInstalledVersion(gaiaBin) });
    return;
  }
  setState(STATES.INSTALLING, { stage: STAGES.AGENT_REFRESH, version });
  makeProgressReporter(opts.onProgress)(STAGES.AGENT_REFRESH, null, "Updating the GAIA agent");

  const result = await runCommand(gaiaBin, ["init", "--agent-only"], {
    stageLabel: "agent",
    env: { ...process.env, COLUMNS: "1000" },
    signal: opts.signal,
  });
  const lastLine = `${result.stderr || ""}`.trim().split(/\r?\n/).pop();
  if (result.code === AGENT_KEPT_EXIT) {
    // Offline: the working agent stays, and the next launch tries the update again.
    log(`Kept the installed GAIA agent; the update runs again next launch: ${lastLine}`);
    setState(STATES.PARTIAL, { stage: STAGES.AGENT_REFRESH, version, message: lastLine });
    return;
  }
  if (result.code !== 0) {
    throw new InstallError(
      lastLine ? lastLine.replace(/^Error:\s*/, "") : `The GAIA agent update exited ${result.code}.`,
      { stage: STAGES.AGENT_REFRESH, code: result.code }
    );
  }
  setState(STATES.READY, { version, installedVersion: getInstalledVersion(gaiaBin) });
}

// ── Backend prefetch for an app update ──────────────────────────────────────

const PREFETCH_DIR = path.join(GAIA_HOME, "cache", "backend-prefetch");

function _prefetchMarker(pep440) {
  return path.join(PREFETCH_DIR, `${pep440}.ready`);
}

/**
 * True when `version`'s backend needs no download: an app that ships its
 * backend gets it with the update, otherwise a prefetch left it in uv's cache.
 */
function isBackendPrefetched(version) {
  if (shippedGaiaBin()) return true;
  return Boolean(version) && fs.existsSync(_prefetchMarker(toPep440(version)));
}

/**
 * Before an app update restarts, put the backend it will need into uv's cache
 * by installing it into a throwaway environment. The first launch then
 * installs offline, so it neither waits on nor depends on PyPI.
 * Throws with uv's own words when the download fails.
 */
async function prefetchBackend(appVersion, { isPackaged } = {}) {
  if (isPackaged) {
    log("This app ships its own backend, so the update carries it and there is nothing to prefetch");
    return;
  }
  if (process.env.GAIA_LOCAL_WHEEL) {
    log("GAIA_LOCAL_WHEEL is set: the next launch installs that wheel, so there is nothing to prefetch");
    return;
  }
  const version = toPep440(appVersion);
  const pipPackage = `amd-gaia[ui]==${version}`;
  const workDir = path.join(PREFETCH_DIR, version);
  const venv = path.join(workDir, "venv");
  const python = IS_WINDOWS ? path.join(venv, "Scripts", "python.exe") : path.join(venv, "bin", "python");
  log(`Prefetching ${pipPackage} for the update`);
  await ensureUv({ isPackaged });
  _removeScratchVenvs();
  fs.mkdirSync(workDir, { recursive: true });
  try {
    const venvResult = await runCommand("uv", ["venv", venv, "--python", "3.12"], { stageLabel: "prefetch" });
    if (venvResult.code !== 0) {
      throw new Error(`uv venv exited ${venvResult.code}: ${_lastLines(venvResult)}`);
    }
    const args = ["pip", "install", pipPackage, "--refresh", "--python", python];
    let result = await runCommand("uv", args, { stageLabel: "prefetch" });
    if (result.code !== 0 && isTlsCertError(`${result.stdout || ""}\n${result.stderr || ""}`)) {
      result = await runCommand("uv", [...args, "--native-tls"], { stageLabel: "prefetch" });
    }
    if (result.code !== 0) {
      throw new Error(`Couldn't download ${pipPackage} (uv exit ${result.code}): ${_lastLines(result)}`);
    }
  } finally {
    fs.rmSync(workDir, { recursive: true, force: true });
  }
  fs.writeFileSync(_prefetchMarker(version), new Date().toISOString());
  log(`Prefetched ${pipPackage}: the next launch installs it offline`);
}

/** Scratch venvs a quit cut short; the .ready markers beside them stay. */
function _removeScratchVenvs() {
  let entries = [];
  try {
    entries = fs.readdirSync(PREFETCH_DIR, { withFileTypes: true });
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
  for (const entry of entries) {
    if (entry.isDirectory()) fs.rmSync(path.join(PREFETCH_DIR, entry.name), { recursive: true, force: true });
  }
}

function _lastLines({ stdout, stderr }) {
  return `${stdout || ""}\n${stderr || ""}`
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .slice(-5)
    .join(" | ");
}

// ── Version-aware ensure ─────────────────────────────────────────────────────

/**
 * Whether the backend is finished at the app's version. A recorded failure or
 * unfinished stage says no, so it reaches ensureBackend and the user.
 */
function isBackendCurrent() {
  // A local wheel is reinstalled on every launch, as ensureBackend says.
  if (process.env.GAIA_LOCAL_WHEEL && !shippedGaiaBin()) return false;
  if (shippedGaiaBin()) {
    // The core is always current; an app version is done once it refreshed the agent.
    if (!bundledGaiaBin()) return false;
    const prev = getState();
    if (!prev) {
      if (fs.existsSync(FLAGSHIP_SENTINEL)) return false;
      // Record this version now, or the agent first-run setup installs reads as an update next launch.
      const version = appVersion();
      setState(STATES.READY, { version, installedVersion: toPep440(version) });
      return true;
    }
    return prev.state === STATES.READY && toPep440(prev.version || "") === toPep440(appVersion());
  }
  const bin = findGaiaBin();
  if (!bin) return false;
  const prev = getState();
  if (prev && prev.state !== STATES.READY) return false;
  return getInstalledVersion(bin) === toPep440(appVersion());
}

/**
 * The installed backend version, or null when it is not installed.
 * For the app's own venv this reads the package record instead of running
 * `gaia --version`, which starts Python and can take 20 s+ on a busy PC.
 */
function getInstalledVersion(gaiaBin) {
  // Built with the app, from the same commit.
  if (shippedGaiaBin() && gaiaBin === bundledGaiaBin()) {
    return toPep440(appVersion());
  }
  if (gaiaBin === GAIA_BIN) {
    // existsSync follows the venv's python symlink, so a removed base interpreter reads as missing.
    if (!fs.existsSync(GAIA_PYTHON_BIN)) {
      log(`${GAIA_PYTHON_BIN} is missing or broken — treating the backend as not installed`);
      return null;
    }
    return venvPackageVersion();
  }
  try {
    const result = spawnSync(gaiaBin, ["--version"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5000,
      windowsHide: true,
    });
    if (result.status === 0 && result.stdout) {
      // Keep an rcN suffix: truncating it would let a release candidate's
      // backend pass for the final's and never be upgraded.
      const match = result.stdout.toString().trim().match(/(\d+\.\d+\.\d+(?:rc\d+)?)/);
      return match ? match[1] : null;
    }
  } catch (err) {
    logError(`Could not run ${gaiaBin} --version: ${err.message}`);
  }
  return null;
}

/** Entries of `dir`, or none when it does not exist. */
function listDir(dir) {
  try {
    return fs.readdirSync(dir);
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

/** The version in the venv's `amd_gaia-<version>.dist-info` record, or null when there is none. */
function venvPackageVersion() {
  const sitePackages = IS_WINDOWS
    ? [path.join(GAIA_VENV, "Lib", "site-packages")]
    : listDir(path.join(GAIA_VENV, "lib")).map((py) =>
        path.join(GAIA_VENV, "lib", py, "site-packages")
      );
  const versions = sitePackages.flatMap((dir) =>
    listDir(dir)
      .map((entry) => /^amd_gaia-(.+)\.dist-info$/.exec(entry))
      .filter(Boolean)
      .map((m) => m[1])
  );
  if (versions.length > 1) {
    log(`More than one amd-gaia install in ${GAIA_VENV_DISPLAY} (${versions.join(", ")}) — treating it as not installed`);
    return null;
  }
  return versions[0] || null;
}

/**
 * Ensure the GAIA backend is installed at the expected version.
 * Returns the path to the gaia binary on success.
 *
 * opts:
 *   - onProgress(stage, percent, message)
 *   - version: override the pinned version
 *   - allowPartialRestart: bool (default true) — restart from scratch if
 *     the state file indicates a `partial` install.
 *   - signal: AbortSignal — stops the running step (the app is quitting)
 *
 * Throws InstallError on failure and updates the state file. Throws
 * InstallCancelled when `signal` stopped it; the state file then still says
 * installing, so the next launch runs the install again.
 */
async function ensureBackend(opts = {}) {
  openLog({ truncate: true });

  try {
    const bundled = await ensureBundledCore();
    if (bundled) {
      log(`Using the GAIA backend this app ships: ${bundled}`);
      await refreshAgent(bundled, opts);
      return bundled;
    }
    if (opts.isPackaged) {
      throw new InstallError("This GAIA install is missing its backend.", {
        stage: STAGES.VERIFY,
        suggestion: `Reinstall GAIA from https://amd-gaia.ai/#install. Expected it at ${BUNDLED_GAIA_BIN}.`,
      });
    }

    const preChecks = await runPreChecks({ onProgress: opts.onProgress });

    // Handle a pre-existing partial install first (before disk/network fails
    // would hide the interrupted state).
    if (preChecks.previousState) {
      const prev = preChecks.previousState;
      if (prev.state === STATES.INSTALLING) {
        // The previous run never finished. Record this and proceed with a
        // fresh restart (per §10.4 recommendation A).
        log(
          `Previous install was interrupted at stage=${prev.stage || "?"} — finishing it`
        );
        setState(STATES.PARTIAL, { stage: prev.stage, message: "Previous install interrupted" });
      } else if (prev.state === STATES.PARTIAL) {
        log("Previous launch detected a partial install — finishing it");
      } else if (prev.state === STATES.FAILED) {
        log(`Previous install failed: ${prev.message || "(no detail)"} — retrying`);
      } else if (prev.state === STATES.READY) {
        log("Previous state: ready");
      }
    }

    // Network check is ADVISORY, never fatal. A failed HEAD probe is most often
    // a corporate TLS-inspection proxy whose root CA isn't in Node's bundled
    // trust store (#1572) — the machine is online and `uv`/`pip`, which honor
    // HTTPS_PROXY and the system trust store, install fine. If the host really
    // is unreachable, the install step below fails loudly with an accurate
    // uv/pip error, which is more actionable than a generic "offline".
    if (!preChecks.network.ok) {
      if (preChecks.network.kind === "tls") {
        log(
          `Network pre-check hit a TLS/certificate error (not offline): ${preChecks.network.message}. ` +
            "Typically a corporate proxy root CA missing from Node's trust store — " +
            "set NODE_EXTRA_CA_CERTS to it (or relaunch with --use-system-ca) if the install fails. Proceeding."
        );
      } else {
        log(
          `Network pre-check could not reach any host: ${preChecks.network.message}. ` +
            "Proceeding anyway; the install step will surface an accurate error if the network is truly unreachable."
        );
      }
    }

    // Fast-path: already installed at the expected version.
    // Skip when expectedVersion is null (GAIA_LOCAL_WHEEL is set) — always
    // reinstall from the local wheel so CI gets a fresh install each run.
    const expectedVersion = resolveBackendVersion(opts);
    const existingBin = findGaiaBin();
    // Installed means the package finished installing. No state file at all
    // is an install from before it existed.
    const prev = preChecks.previousState;
    const packageFinished =
      !prev ||
      prev.state === STATES.READY ||
      prev.stage === LEGACY_SETUP_STAGE ||
      prev.stage === STAGES.VERIFY ||
      prev.stage === STAGES.AGENT_REFRESH;
    let installedVersion = null;
    if (existingBin) {
      installedVersion = getInstalledVersion(existingBin);
      const versionMatches =
        expectedVersion !== null && installedVersion === toPep440(expectedVersion);
      if (versionMatches && packageFinished && prev && prev.stage === STAGES.AGENT_REFRESH) {
        await refreshAgent(existingBin, opts);
        return existingBin;
      }
      if (versionMatches && packageFinished) {
        log(
          `GAIA backend already installed at version ${installedVersion} — nothing to do`
        );
        setState(STATES.READY, { version: expectedVersion, installedVersion });
        // Tell the UI we are instantly ready.
        const report = makeProgressReporter(opts.onProgress);
        report(STAGES.VERIFY, 100, `GAIA ${installedVersion} ready`);
        return existingBin;
      }
      log(
        versionMatches
          ? `An earlier install stopped at stage=${prev.stage || "?"} — reinstalling`
          : `Version mismatch: expected=${expectedVersion} installed=${installedVersion || "unknown"} — upgrading`
      );
    } else {
      log("GAIA backend not found — installing from scratch");
    }

    // Checked only once there is something to install, so a full disk never stops a current install.
    if (!preChecks.disk.ok) {
      const keep = Boolean(existingBin) && packageFinished && prev?.state !== STATES.FAILED;
      throw lowDiskError(preChecks.disk, { keep, installedVersion });
    }

    await installBackend(opts);

    const verified = findGaiaBin();
    if (!verified) {
      const err = new InstallError(
        "GAIA backend not found after installation.",
        {
          stage: STAGES.VERIFY,
          suggestion: "Check the log file and try reinstalling. See https://amd-gaia.ai/docs/quickstart",
        }
      );
      setState(STATES.FAILED, {
        stage: STAGES.VERIFY,
        message: err.message,
      });
      throw err;
    }

    await refreshAgent(verified, opts);
    return verified;
  } catch (err) {
    if (err instanceof InstallCancelled) {
      log(err.message);
      throw err;
    }
    if (err instanceof InstallError) {
      if (err.keepState) {
        log(`Install refused; the recorded state is unchanged: ${err.message}`);
        throw err;
      }
      setState(STATES.FAILED, {
        stage: err.stage || null,
        message: err.message,
      });
      throw err;
    }
    // Unexpected — still mark failed and wrap.
    logError(`Unexpected error during ensureBackend: ${err.message}`);
    setState(STATES.FAILED, { message: err.message });
    throw new InstallError(`Unexpected error: ${err.message}`, {
      suggestion: "Check the log file for details.",
    });
  } finally {
    closeLog();
  }
}

// ── Exports ──────────────────────────────────────────────────────────────────

module.exports = {
  // Core API
  ensureUv,
  installBackend,
  ensureBackend,
  isBackendCurrent,
  prefetchBackend,
  isBackendPrefetched,
  getInstalledVersion,
  findGaiaBin,
  toPep440,
  stopRunningDaemon,

  // Pre-checks
  runPreChecks,
  checkDiskSpace,
  checkNetwork,
  isFileLockedError,
  isTransientNetworkError,
  isTlsCertError,
  buildCaBundle,
  proxyForHttps,
  classifyNetworkError,
  _checkOneHost,

  // State machine
  getState,
  setState,
  clearState,

  computeOverallPercent,

  // Logging
  openLog,
  closeLog,
  log,
  logError,
  getLogPath,
  getStatePath,
  readLogTail,

  // Constants
  STATES,
  STAGES,
  FAILURE_REASONS,
  DAEMON_STOP_TIMEOUT_MS,
  DAEMON_STOP_LEMONADE_EXIT,
  GAIA_HOME,
  GAIA_VENV,
  GAIA_BIN,
  MIN_DISK_SPACE_BYTES,

  // Error
  InstallError,
  InstallCancelled,
};
