// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * backend-installer.cjs — Shared GAIA Python backend bootstrap logic.
 *
 * Single source of truth for installing / upgrading the GAIA Python backend
 * (`~/.gaia/venv` with `amd-gaia[ui]==<pinned-version>`). Called from both:
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
 *   - getInstalledVersion(gaiaBin)   → string | null
 *   - findGaiaBin()                  → string | null
 *   - getState() / setState()        → state machine helpers
 *   - getLogPath() / getStatePath()  → path helpers
 *   - runPreChecks(opts)             → Promise<PreCheckResult>
 *   - STATES                         → state name constants
 *
 * Progress callbacks are invoked as `onProgress(stage, percent, message)` —
 * the module never touches Electron APIs, so the caller (main.cjs) is
 * responsible for rendering the progress UI.
 */

"use strict";

const { spawn, spawnSync, execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const https = require("https");

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

const STATE_FILE = path.join(GAIA_HOME, "electron-install-state.json");
const LOG_FILE = path.join(GAIA_HOME, "electron-install.log");

const MIN_DISK_SPACE_BYTES = 3 * 1024 * 1024 * 1024; // 3 GB
const NETWORK_CHECK_URL = "https://astral.sh";
const NETWORK_CHECK_TIMEOUT_MS = 5000;

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
  GAIA_INIT: "gaia-init",
  VERIFY: "verify",
});

// Weight each stage contributes to the overall 0-100 progress.
// Sum must equal 100.
const STAGE_WEIGHTS = {
  [STAGES.PRE_CHECKS]: 2,
  [STAGES.ENSURE_UV]: 8,
  [STAGES.CREATE_VENV]: 10,
  [STAGES.INSTALL_PACKAGE]: 50,
  [STAGES.GAIA_INIT]: 28,
  [STAGES.VERIFY]: 2,
};

const STAGE_ORDER = [
  STAGES.PRE_CHECKS,
  STAGES.ENSURE_UV,
  STAGES.CREATE_VENV,
  STAGES.INSTALL_PACKAGE,
  STAGES.GAIA_INIT,
  STAGES.VERIFY,
];

// ── Logging ──────────────────────────────────────────────────────────────────

let logStream = null;

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
    if (truncate) {
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
    const percent = computeOverallPercent(stage, withinStagePercent);
    try {
      safe(stage, percent, message || "");
    } catch (err) {
      logError(`onProgress callback threw: ${err.message}`);
    }
  };
}

// ── Command helpers ──────────────────────────────────────────────────────────

/**
 * Check if a command exists on PATH.
 */
function commandExists(cmd) {
  try {
    const check = IS_WINDOWS ? `where ${cmd}` : `command -v ${cmd}`;
    execSync(check, { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

/**
 * Find the gaia binary — prefer the managed venv, fall back to PATH.
 */
function findGaiaBin() {
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
 */
function runCommand(cmd, args, { env, stageLabel } = {}) {
  return new Promise((resolve) => {
    log(`$ ${cmd} ${args.join(" ")}`);
    let proc;
    try {
      proc = spawn(cmd, args, {
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

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (data) => {
      const chunk = data.toString();
      stdout += chunk;
      chunk.split(/\r?\n/).forEach((line) => {
        if (line) log(`  ${stageLabel ? `[${stageLabel}] ` : ""}${line}`);
      });
    });

    proc.stderr.on("data", (data) => {
      const chunk = data.toString();
      stderr += chunk;
      chunk.split(/\r?\n/).forEach((line) => {
        if (line) log(`  ${stageLabel ? `[${stageLabel}] ` : ""}${line}`);
      });
    });

    proc.on("error", (err) => {
      logError(`${cmd} error: ${err.message}`);
      resolve({ code: -1, stdout, stderr, error: err });
    });

    proc.on("exit", (code) => {
      log(`  exit code: ${code}`);
      resolve({ code, stdout, stderr });
    });
  });
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

  // Fallback: platform-specific shell commands. Non-fatal if unavailable.
  try {
    if (IS_WINDOWS) {
      // Use PowerShell to read free space on the drive containing parent.
      const drive = path.parse(parent).root.replace(/\\$/, "");
      const out = execSync(
        `powershell -NoProfile -Command "(Get-PSDrive -Name '${drive.replace(":", "")}').Free"`,
        { encoding: "utf8", timeout: 5000 }
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
      const out = execSync(`df -k "${parent}"`, { encoding: "utf8", timeout: 5000 });
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
 * Best-effort network reachability check. Performs a HEAD request to
 * https://astral.sh (where the uv installer lives). Resolves { ok, message? }.
 */
function checkNetwork() {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    try {
      const req = https.request(
        NETWORK_CHECK_URL,
        {
          method: "HEAD",
          timeout: NETWORK_CHECK_TIMEOUT_MS,
          headers: { "User-Agent": "gaia-backend-installer/1.0" },
        },
        (res) => {
          // Any response (even 3xx/4xx) means we have basic connectivity.
          res.resume();
          finish({ ok: true, status: res.statusCode });
        }
      );
      req.on("timeout", () => {
        req.destroy();
        finish({
          ok: false,
          message: `Network check timed out after ${NETWORK_CHECK_TIMEOUT_MS / 1000}s`,
        });
      });
      req.on("error", (err) => {
        finish({
          ok: false,
          message: `Network check failed: ${err.message}`,
        });
      });
      req.end();
    } catch (err) {
      finish({
        ok: false,
        message: `Network check threw: ${err.message}`,
      });
    }
  });
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
  constructor(message, { stage, code, suggestion } = {}) {
    super(message);
    this.name = "InstallError";
    this.stage = stage || null;
    this.code = code || null;
    this.suggestion = suggestion || null;
  }
}

/**
 * Ensure `uv` is available. Installs it from astral.sh if missing.
 * Throws an InstallError if installation fails.
 */
async function ensureUv({ onProgress } = {}) {
  const report = makeProgressReporter(onProgress);

  if (commandExists("uv")) {
    log("uv already installed");
    report(STAGES.ENSURE_UV, 100, "uv is already installed");
    return;
  }

  report(STAGES.ENSURE_UV, 0, "Installing uv (Python package manager)");
  log("Installing uv...");

  let result;
  if (IS_WINDOWS) {
    result = await runCommand(
      "powershell",
      ["-ExecutionPolicy", "Bypass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"],
      { stageLabel: "uv-install" }
    );
  } else {
    result = await runCommand(
      "bash",
      ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
      { stageLabel: "uv-install" }
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

  // On some shells, PATH isn't refreshed for the current process. Re-check
  // after adding the default uv install dirs. uv has shipped under both
  // ~/.cargo/bin and ~/.local/bin at different times — most notably the
  // Windows installer migrated from .cargo/bin to %USERPROFILE%\.local\bin
  // in early 2025. Try BOTH paths on every platform to be robust against
  // installer version drift.
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
  log("uv install complete");
}

// ── Backend install ──────────────────────────────────────────────────────────

/**
 * Read the pinned backend version from package.json (or a caller override).
 */
function resolveBackendVersion(opts = {}) {
  if (opts.version) return opts.version;
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

/**
 * Install the GAIA Python backend from scratch.
 *
 * opts:
 *   - onProgress(stage, percent, message)
 *   - version: string — override the pinned version
 *   - skipGaiaInit: boolean — skip `gaia init` (for testing)
 *
 * Throws InstallError on failure. The state file is updated to reflect
 * the current stage so a subsequent launch can recover.
 */
async function installBackend(opts = {}) {
  const report = makeProgressReporter(opts.onProgress);
  const version = resolveBackendVersion(opts);
  const pipPackage = `amd-gaia[ui]==${version}`;

  log("================================================");
  log("  Installing GAIA backend");
  log("================================================");
  log(`Package: ${pipPackage}`);
  log(`Location: ${GAIA_VENV_DISPLAY}`);

  setState(STATES.INSTALLING, { stage: STAGES.ENSURE_UV, version });

  // Stage 1: ensure uv
  await ensureUv({ onProgress: opts.onProgress });

  // Stage 2: create venv
  setState(STATES.INSTALLING, { stage: STAGES.CREATE_VENV, version });
  report(STAGES.CREATE_VENV, 0, "Creating Python 3.12 environment");

  ensureGaiaHome();

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

    const venvResult = await runCommand(
      "uv",
      ["venv", GAIA_VENV, "--python", "3.12"],
      { stageLabel: "venv" }
    );
    if (venvResult.code !== 0) {
      throw new InstallError(
        `Failed to create Python environment (uv venv exit ${venvResult.code}).`,
        {
          stage: STAGES.CREATE_VENV,
          code: venvResult.code,
          suggestion: `Try creating it manually:\n  uv venv ${GAIA_VENV_DISPLAY} --python 3.12\nThen restart GAIA.`,
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

  const pipArgs = [
    "pip",
    "install",
    pipPackage,
    "--refresh",
    "--python",
    GAIA_PYTHON_BIN,
  ];
  // Linux/macOS: use CPU-only PyTorch to avoid huge CUDA wheels.
  if (!IS_WINDOWS) {
    pipArgs.push("--extra-index-url", "https://download.pytorch.org/whl/cpu");
  }

  const installResult = await runCommand("uv", pipArgs, { stageLabel: "pip" });
  if (installResult.code !== 0) {
    throw new InstallError(
      `Failed to install ${pipPackage} (pip exit ${installResult.code}).`,
      {
        stage: STAGES.INSTALL_PACKAGE,
        code: installResult.code,
        suggestion: `Try installing manually:\n  uv pip install ${pipPackage} --python ${
          IS_WINDOWS ? `${GAIA_VENV_DISPLAY}/Scripts/python.exe` : `${GAIA_VENV_DISPLAY}/bin/python`
        }\nThen restart GAIA. See https://amd-gaia.ai/quickstart#cli-install`,
      }
    );
  }

  if (!fs.existsSync(GAIA_BIN)) {
    throw new InstallError(
      `GAIA binary not found at ${GAIA_VENV_DISPLAY} after install.`,
      {
        stage: STAGES.INSTALL_PACKAGE,
        suggestion: "The package was installed but the gaia executable is missing. Try reinstalling from https://amd-gaia.ai/quickstart",
      }
    );
  }
  report(STAGES.INSTALL_PACKAGE, 100, "GAIA package installed");

  // Stage 4: gaia init
  if (!opts.skipGaiaInit) {
    setState(STATES.INSTALLING, { stage: STAGES.GAIA_INIT, version });
    report(
      STAGES.GAIA_INIT,
      0,
      "Setting up Lemonade Server and downloading models (this can take several minutes)"
    );

    const initResult = await runCommand(
      GAIA_BIN,
      ["init", "--profile", "minimal"],
      { stageLabel: "gaia-init" }
    );

    if (initResult.code !== 0) {
      // gaia init failure is non-fatal (user can retry later), but we still
      // log it and treat the rest of the install as successful.
      log(
        `Warning: gaia init exited with code ${initResult.code}. Continuing anyway.`
      );
    }
    report(STAGES.GAIA_INIT, 100, "Lemonade Server setup complete");
  } else {
    log("Skipping gaia init (skipGaiaInit=true)");
  }

  // Stage 5: verify
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

  setState(STATES.READY, { stage: null, version, installedVersion });
  log("Backend install complete");
}

// ── Version-aware ensure ─────────────────────────────────────────────────────

/**
 * Run `<gaiaBin> --version` and extract the installed version string.
 * Returns null on failure.
 */
function getInstalledVersion(gaiaBin) {
  try {
    const result = spawnSync(gaiaBin, ["--version"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 5000,
      windowsHide: true,
    });
    if (result.status === 0 && result.stdout) {
      const match = result.stdout.toString().trim().match(/(\d+\.\d+\.\d+)/);
      return match ? match[1] : null;
    }
  } catch {
    // ignore
  }
  return null;
}

/**
 * Ensure the GAIA backend is installed at the expected version.
 * Returns the path to the gaia binary on success.
 *
 * opts:
 *   - onProgress(stage, percent, message)
 *   - version: override the pinned version
 *   - skipGaiaInit: bool
 *   - allowPartialRestart: bool (default true) — restart from scratch if
 *     the state file indicates a `partial` install.
 *
 * Throws InstallError on failure and updates the state file.
 */
async function ensureBackend(opts = {}) {
  openLog({ truncate: true });

  try {
    const preChecks = await runPreChecks({ onProgress: opts.onProgress });

    // Handle a pre-existing partial install first (before disk/network fails
    // would hide the interrupted state).
    if (preChecks.previousState) {
      const prev = preChecks.previousState;
      if (prev.state === STATES.INSTALLING) {
        // The previous run never finished. Record this and proceed with a
        // fresh restart (per §10.4 recommendation A).
        log(
          `Previous install was interrupted at stage=${prev.stage || "?"} — restarting from scratch`
        );
        setState(STATES.PARTIAL, { stage: prev.stage, message: "Previous install interrupted" });
      } else if (prev.state === STATES.PARTIAL) {
        log("Previous launch detected a partial install — restarting from scratch");
      } else if (prev.state === STATES.FAILED) {
        log(`Previous install failed: ${prev.message || "(no detail)"} — retrying`);
      } else if (prev.state === STATES.READY) {
        log("Previous state: ready");
      }
    }

    // Disk check failure: fatal, surface as InstallError.
    if (!preChecks.disk.ok) {
      const freeMb =
        preChecks.disk.freeBytes != null
          ? Math.round(preChecks.disk.freeBytes / (1024 * 1024))
          : null;
      const requiredMb = Math.round(
        preChecks.disk.requiredBytes / (1024 * 1024)
      );
      const err = new InstallError(
        `Not enough free disk space. Required: ${requiredMb} MB${
          freeMb != null ? `, available: ${freeMb} MB` : ""
        }.`,
        {
          stage: STAGES.PRE_CHECKS,
          suggestion: `Free at least ${requiredMb} MB at ${path.dirname(
            GAIA_HOME
          )} and try again.`,
        }
      );
      setState(STATES.FAILED, { stage: STAGES.PRE_CHECKS, message: err.message });
      throw err;
    }

    // Network check failure: fatal, surface as InstallError.
    if (!preChecks.network.ok) {
      const err = new InstallError(
        `Could not reach ${NETWORK_CHECK_URL}. You appear to be offline.`,
        {
          stage: STAGES.PRE_CHECKS,
          suggestion:
            "Connect to the internet and try again. If you are behind a corporate proxy, configure HTTPS_PROXY and re-launch GAIA.",
        }
      );
      setState(STATES.FAILED, { stage: STAGES.PRE_CHECKS, message: err.message });
      throw err;
    }

    // Fast-path: already installed at the expected version.
    const expectedVersion = resolveBackendVersion(opts);
    const existingBin = findGaiaBin();
    if (existingBin) {
      const installedVersion = getInstalledVersion(existingBin);
      if (installedVersion === expectedVersion) {
        log(
          `GAIA backend already installed at version ${installedVersion} — nothing to do`
        );
        setState(STATES.READY, {
          version: expectedVersion,
          installedVersion,
        });
        // Tell the UI we are instantly ready.
        const report = makeProgressReporter(opts.onProgress);
        report(STAGES.VERIFY, 100, `GAIA ${installedVersion} ready`);
        return existingBin;
      }
      log(
        `Version mismatch: expected=${expectedVersion} installed=${installedVersion || "unknown"} — upgrading`
      );
    } else {
      log("GAIA backend not found — installing from scratch");
    }

    await installBackend(opts);

    const verified = findGaiaBin();
    if (!verified) {
      const err = new InstallError(
        "GAIA backend not found after installation.",
        {
          stage: STAGES.VERIFY,
          suggestion: "Check the log file and try reinstalling. See https://amd-gaia.ai/quickstart",
        }
      );
      setState(STATES.FAILED, {
        stage: STAGES.VERIFY,
        message: err.message,
      });
      throw err;
    }

    return verified;
  } catch (err) {
    if (err instanceof InstallError) {
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
  getInstalledVersion,
  findGaiaBin,

  // Pre-checks
  runPreChecks,
  checkDiskSpace,
  checkNetwork,

  // State machine
  getState,
  setState,
  clearState,

  // Logging
  openLog,
  closeLog,
  log,
  logError,
  getLogPath,
  getStatePath,

  // Constants
  STATES,
  STAGES,
  GAIA_HOME,
  GAIA_VENV,
  GAIA_BIN,
  MIN_DISK_SPACE_BYTES,

  // Error
  InstallError,
};
