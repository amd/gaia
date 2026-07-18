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
const crypto = require("crypto");
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

const STATE_FILE = path.join(GAIA_HOME, "electron-install-state.json");
const LOG_FILE = path.join(GAIA_HOME, "electron-install.log");

// 5 GB — PyTorch wheels have grown significantly and `gaia init` downloads
// additional model data on first run; 3 GB is no longer enough headroom.
const MIN_DISK_SPACE_BYTES = 5 * 1024 * 1024 * 1024; // 5 GB
const NETWORK_CHECK_HOSTS = Object.freeze([
  "https://pypi.org/simple/",
  "https://astral.sh",
]);
const NETWORK_CHECK_TIMEOUT_MS = 5000;

// Pip-install resilience: the install stage fetches heavy transitive deps from
// PyPI, so retry a transient network failure a few times before giving up.
const INSTALL_MAX_ATTEMPTS = 3;
const INSTALL_RETRY_BACKOFF_MS = 3000;

// ── Bundled `uv` binary ──────────────────────────────────────────────────────
//
// Issue #782 / T3: the AppImage now ships a pinned `uv` under
// `extraResources` (see electron-builder.yml). At runtime we copy it into
// `~/.gaia/bin/uv` with atomic-rename + SHA256 verification. The previous
// `curl | sh` path is retained only as an unpackaged-dev fallback so
// contributors running from source keep working.
//
// When bumping uv, update:
//   - .github/workflows/build-installers.yml (tarball .tar.gz SHA256 — archive)
//   - BUNDLED_UV_SHA256 below (extracted binary SHA256, linux-x64/win-x64 only)
//
// IMPORTANT: per-platform verification strategy differs:
//   - linux-x64 / win-x64: BUNDLED_UV_SHA256 pins the raw extracted-binary
//     digest (no post-build modification) — deterministic across CI runs.
//   - mac-arm64: NOT pinned here. electron-builder code-signs the bundled uv
//     during packaging (ad-hoc `identity=-` when no Developer ID cert is
//     configured, which is every CI build today), and ad-hoc codesign output
//     depends on the codesign/Xcode toolchain baked into the GitHub-hosted
//     `macos-latest` runner image — which floats and is NOT reproducible
//     across CI runs (observed: macos-15-arm64 vs macos-26-arm64 images
//     produced two different digests for byte-identical source). A fixed
//     SHA256 pin is therefore not deterministic on macOS and would fail
//     ~half of CI runs and brick first-launch on user machines whenever the
//     runner image rolls. Instead, ensureUv() and the dmg-structural-smoke
//     test both run `codesign --verify --strict` against the bundled binary
//     — this validates the on-disk signature is intact/untampered without
//     depending on the exact signing bytes, and still fails loud on
//     corruption or an actually-invalid signature.
const BUNDLED_UV_VERSION = "0.5.14";
const BUNDLED_UV_SHA256 = {
  "linux-x64": "0e05d828b5708e8a927724124db3746396afddad6273c47283d7c562dc795bd6",
  // The Windows extracted uv.exe SHA is populated by CI during the
  // build step. The placeholder MUST be replaced in CI before packaging
  // so runtime verification remains strict.
  "win-x64": "055d55eec85a91cfb5e9c8bc7f6463f9883866796c5bcb205fbcdfed9c088c88",
  // mac-arm64: intentionally absent. See the comment block above — the
  // post-codesign digest is not deterministic across CI runner images, so
  // mac-arm64 is verified via codesignVerify() (identity/signature validity)
  // instead of a fixed SHA256 pin. bundledUvPlatformKey() still returns
  // "mac-arm64"; callers must not treat a missing entry here as "unpinned
  // platform" for darwin — see ensureUv()/installBundledUv().
};

const MANAGED_UV_DIR = path.join(GAIA_HOME, "bin");
const MANAGED_UV_BIN = IS_WINDOWS
  ? path.join(MANAGED_UV_DIR, "uv.exe")
  : path.join(MANAGED_UV_DIR, "uv");

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

/**
 * Log rotation is a session-level concern: we want a fresh log on the first
 * `ensureBackend` call of a given process, but NOT on subsequent retries
 * within the same session, because the original failure log is what the
 * user needs to attach to a bug report after clicking Retry. Flipping this
 * to `true` is a one-way operation; subsequent `openLog({ truncate: true })`
 * calls turn into plain appends.
 */
let logRotatedThisSession = false;

function isTruthyEnv(value) {
  return /^(1|true|yes|on)$/i.test(String(value || ""));
}

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

/**
 * Detect the Windows "file in use" signature in command output. An upgrade's
 * `uv pip install --refresh` can't replace gaia.exe while a previous GAIA
 * process still holds it open — pip reports os error 32 (issue #1388).
 */
function isFileLockedError(output) {
  if (!output) return false;
  return /os error 32/i.test(output) || /being used by another process/i.test(output);
}

/**
 * Detect a transient network failure in `uv pip install` output so the
 * install stage can retry instead of failing the whole bootstrap. The
 * install stage downloads heavy transitive deps (scipy, numpy, torch) from
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
    log(`Could not read NODE_EXTRA_CA_CERTS (${file}): ${err.message}`);
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
    finish({ ok: false, kind: "connectivity", message: `${target.href}: invalid proxy URL "${proxy}"` });
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
  constructor(message, { stage, code, suggestion } = {}) {
    super(message);
    this.name = "InstallError";
    this.stage = stage || null;
    this.code = code || null;
    this.suggestion = suggestion || null;
  }
}

/**
 * Which `extraResources` subdirectory holds the bundled uv for this host.
 * Returns null for platforms we don't yet ship a binary for (falls through
 * to the dev fallback).
 */
function bundledUvPlatformKey() {
  if (process.platform === "linux" && process.arch === "x64") return "linux-x64";
  if (process.platform === "win32" && process.arch === "x64") return "win-x64";
  if (process.platform === "darwin" && process.arch === "arm64") return "mac-arm64";
  return null;
}

/**
 * Stream-SHA256 a file. Returns lowercase hex.
 */
function sha256File(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    const stream = fs.createReadStream(filePath);
    stream.on("error", reject);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", () => resolve(hash.digest("hex")));
  });
}

/**
 * Verify a macOS binary's code signature is structurally intact via
 * `codesign --verify --strict`. Used in place of a fixed SHA256 pin for
 * mac-arm64 (see the BUNDLED_UV_SHA256 comment) — ad-hoc-signed binaries
 * (identity=-, which is every CI build today) pass this check, but a
 * corrupted, tampered, or actually-unsigned binary fails it. This does NOT
 * depend on the specific bytes any given codesign/Xcode toolchain produces,
 * so it is stable across GitHub-hosted runner image rollovers where a fixed
 * digest pin is not.
 *
 * @param {string} binPath
 * @returns {{ ok: boolean, output: string }}
 */
function codesignVerify(binPath) {
  const result = spawnSync("codesign", ["--verify", "--strict", binPath], {
    encoding: "utf8",
  });
  const output = `${result.stdout || ""}${result.stderr || ""}`.trim();
  return { ok: result.status === 0, output };
}

/**
 * Resolve the bundled uv binary path inside the Electron resources dir.
 * Returns null if this isn't an Electron-packaged runtime (no
 * `process.resourcesPath`) or if the host platform isn't bundled.
 */
function findBundledUvResource() {
  const key = bundledUvPlatformKey();
  if (!key) return null;
  const resourcesPath = process.resourcesPath;
  if (!resourcesPath) return null;
  const candidate = path.join(
    resourcesPath,
    "vendor",
    "uv",
    key,
    IS_WINDOWS ? "uv.exe" : "uv"
  );
  return fs.existsSync(candidate) ? candidate : null;
}

/**
 * Atomically install the bundled uv into ~/.gaia/bin/uv after verifying it.
 * Returns the installed path.
 *
 * Verification strategy differs by platform (see the BUNDLED_UV_SHA256
 * comment): mac-arm64 uses `codesign --verify --strict` (darwin only —
 * post-codesign SHA256 is not deterministic across CI runner images);
 * linux-x64 / win-x64 use the SHA256 pin in BUNDLED_UV_SHA256, which IS
 * deterministic for those platforms (no post-build re-signing).
 *
 * Writes to `uv.tmp-<pid>-<rand>` with mode 0o700, verifies, `chmod +x`,
 * then `fs.rename()` (atomic on same filesystem).
 */
async function installBundledUv(sourcePath, platformKey) {
  const isDarwin = platformKey === "mac-arm64";
  const expected = BUNDLED_UV_SHA256[platformKey];
  if (!isDarwin && (!expected || expected.startsWith("<"))) {
    // Enforce strict verification: builds MUST populate the expected SHA
    // for packaged binaries. Failing fast prevents shipping an unverified
    // uv binary which would be a supply-chain regression.
    throw new InstallError(
      `No bundled uv checksum registered for platform ${platformKey}. Build must populate BUNDLED_UV_SHA256.${platformKey}`,
      { stage: STAGES.ENSURE_UV }
    );
  }

  ensureGaiaHome();
  try {
    fs.mkdirSync(MANAGED_UV_DIR, { recursive: true });
  } catch (err) {
    throw new InstallError(
      `Could not create ${MANAGED_UV_DIR}: ${err.message}`,
      { stage: STAGES.ENSURE_UV }
    );
  }

  const rand = crypto.randomBytes(6).toString("hex");
  const tmpPath = path.join(
    MANAGED_UV_DIR,
    `uv.tmp-${process.pid}-${rand}${IS_WINDOWS ? ".exe" : ""}`
  );

  // Copy source → tmp with restrictive mode.
  await new Promise((resolve, reject) => {
    const rs = fs.createReadStream(sourcePath);
    const ws = fs.createWriteStream(tmpPath, { mode: 0o700 });
    rs.on("error", reject);
    ws.on("error", reject);
    ws.on("finish", resolve);
    rs.pipe(ws);
  });

  if (isDarwin) {
    // chmod BEFORE codesign --verify: codesign needs the execute bit to
    // resolve the binary's designated requirement on some toolchains.
    try {
      fs.chmodSync(tmpPath, 0o700);
    } catch (err) {
      log(`Warning: chmod on tmp uv failed: ${err.message}`);
    }
    const { ok, output } = codesignVerify(tmpPath);
    if (!ok) {
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
      throw new InstallError(
        `Bundled uv failed code signature verification (codesign --verify --strict): ${output || "no output"}`,
        {
          stage: STAGES.ENSURE_UV,
          suggestion:
            "The AppImage/installer may be corrupt. Re-download from https://amd-gaia.ai and try again.",
        }
      );
    }
    log("Bundled uv passed codesign --verify --strict");
  } else {
    let actual;
    try {
      actual = await sha256File(tmpPath);
    } catch (err) {
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
      throw new InstallError(
        `Could not hash copied uv binary: ${err.message}`,
        { stage: STAGES.ENSURE_UV }
      );
    }

    if (actual !== expected) {
      try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
      throw new InstallError(
        `Bundled uv SHA256 mismatch (expected ${expected}, got ${actual}).`,
        {
          stage: STAGES.ENSURE_UV,
          suggestion:
            "The AppImage/installer may be corrupt. Re-download from https://amd-gaia.ai and try again.",
        }
      );
    }

    try {
      if (!IS_WINDOWS) fs.chmodSync(tmpPath, 0o700);
    } catch (err) {
      log(`Warning: chmod on tmp uv failed: ${err.message}`);
    }
  }

  try {
    // rename() is atomic on the same filesystem on POSIX; on Windows
    // it requires the target not to exist, so unlink first.
    if (IS_WINDOWS && fs.existsSync(MANAGED_UV_BIN)) {
      try { fs.unlinkSync(MANAGED_UV_BIN); } catch { /* ignore */ }
    }
    fs.renameSync(tmpPath, MANAGED_UV_BIN);
  } catch (err) {
    try { fs.unlinkSync(tmpPath); } catch { /* ignore */ }
    throw new InstallError(
      `Could not install uv to ${MANAGED_UV_BIN}: ${err.message}`,
      { stage: STAGES.ENSURE_UV }
    );
  }

  log(`Installed bundled uv v${BUNDLED_UV_VERSION} → ${MANAGED_UV_BIN}`);
  return MANAGED_UV_BIN;
}

/**
 * Prepend ~/.gaia/bin to this process's PATH so child spawns see our
 * managed uv before any system-wide install.
 */
function addManagedBinToPath() {
  if (
    process.env.PATH &&
    !process.env.PATH.split(path.delimiter).includes(MANAGED_UV_DIR)
  ) {
    process.env.PATH = `${MANAGED_UV_DIR}${path.delimiter}${process.env.PATH}`;
    log(`Prepended ${MANAGED_UV_DIR} to PATH for this process`);
  }
}

/**
 * Ensure `uv` is available. Preference order (per issue #782 / T3):
 *   1. Managed copy at ~/.gaia/bin/uv already verified (warm-install fast path):
 *      SHA256 pin on linux-x64/win-x64, `codesign --verify --strict` on mac-arm64.
 *   2. Bundled binary in process.resourcesPath/vendor/uv/<platform>/uv:
 *      copy atomically to ~/.gaia/bin/uv with the same platform-appropriate
 *      verification.
 *   3. DEV-ONLY fallback (app.isPackaged === false OR no resourcesPath):
 *      the original `curl | sh` from astral.sh. Not a shipped-user path.
 *   4. System `uv` on PATH (last resort — unverified version).
 *
 * Throws InstallError on failure.
 */
async function ensureUv({ onProgress, isPackaged } = {}) {
  const report = makeProgressReporter(onProgress);
  report(STAGES.ENSURE_UV, 0, "Checking uv (Python package manager)");

  const platformKey = bundledUvPlatformKey();
  const isDarwin = platformKey === "mac-arm64";
  const expectedSha = platformKey ? BUNDLED_UV_SHA256[platformKey] : null;

  // Fast path: warm install already on disk and still passes verification.
  if (isDarwin && fs.existsSync(MANAGED_UV_BIN)) {
    const { ok, output } = codesignVerify(MANAGED_UV_BIN);
    if (ok) {
      log(`Managed uv at ${MANAGED_UV_BIN} passed codesign --verify --strict — reusing`);
      addManagedBinToPath();
      report(STAGES.ENSURE_UV, 100, "uv ready (cached)");
      return;
    }
    log(`Managed uv failed codesign verification (${output || "no output"}) — replacing`);
  } else if (expectedSha && fs.existsSync(MANAGED_UV_BIN)) {
    try {
      const actual = await sha256File(MANAGED_UV_BIN);
      if (actual === expectedSha) {
        log(`Managed uv at ${MANAGED_UV_BIN} passed SHA256 check — reusing`);
        addManagedBinToPath();
        report(STAGES.ENSURE_UV, 100, "uv ready (cached)");
        return;
      }
      log(
        `Managed uv hash mismatch (expected ${expectedSha}, got ${actual}) — replacing`
      );
    } catch (err) {
      log(`Could not verify managed uv: ${err.message} — replacing`);
    }
  }

  // Bundled path (the shipped-user path — AppImage, NSIS, DMG).
  const bundled = findBundledUvResource();
  if (bundled && platformKey) {
    report(STAGES.ENSURE_UV, 30, "Installing bundled uv");
    log(`Using bundled uv from ${bundled}`);

    // Verify the source resource before copying — catches installer
    // corruption before we touch the user's home. mac-arm64 uses
    // codesign --verify --strict (see BUNDLED_UV_SHA256 comment for why a
    // fixed digest isn't viable there); other platforms use the SHA256 pin,
    // which CI must have populated — a missing/placeholder value is a
    // build-time error, rejected at runtime here.
    if (isDarwin) {
      const { ok, output } = codesignVerify(bundled);
      if (!ok) {
        throw new InstallError(
          `Bundled uv resource failed code signature verification (codesign --verify --strict): ${output || "no output"}`,
          {
            stage: STAGES.ENSURE_UV,
            suggestion:
              "The installer appears to be corrupt. Re-download GAIA from https://amd-gaia.ai and try again.",
          }
        );
      }
    } else {
      const srcHash = await sha256File(bundled);
      if (srcHash !== expectedSha) {
        throw new InstallError(
          `Bundled uv resource SHA256 mismatch (expected ${expectedSha}, got ${srcHash}).`,
          {
            stage: STAGES.ENSURE_UV,
            suggestion:
              "The installer appears to be corrupt. Re-download GAIA from https://amd-gaia.ai and try again.",
          }
        );
      }
    }
    await installBundledUv(bundled, platformKey);
    addManagedBinToPath();
    report(STAGES.ENSURE_UV, 100, "uv installed (bundled)");
    return;
  }

  // DEV-ONLY fallback for contributors running from source (no
  // extraResources, no packaged app). Never fires for end users.
  const isDev = isPackaged === false || !process.resourcesPath;
  if (isDev) {
    if (commandExists("uv")) {
      log("uv already on PATH (dev) — using system install");
      report(STAGES.ENSURE_UV, 100, "uv is already installed (system)");
      return;
    }

    log("[dev] No bundled uv and no system uv — falling back to curl|sh installer");
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
        { stageLabel: "uv-install-dev" }
      );
    } else {
      result = await runCommand(
        "bash",
        ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        { stageLabel: "uv-install-dev" }
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

    report(STAGES.ENSURE_UV, 100, "uv installed (dev fallback)");
    return;
  }

  // Packaged Windows rescue: try the Astral PowerShell installer even when
  // running a packaged build. This provides an automated recovery path for
  // end users on clean machines that don't have uv and where the installer
  // build unexpectedly omitted a bundled binary. It is a last-resort and
  // non-fatal attempt; on failure we fall through to the generic error.
  if (IS_WINDOWS && !isDev) {
    log("Packaged Windows: attempting automated uv installer (rescue)");
    try {
      const rescue = await runCommand(
        "powershell",
        [
          "-ExecutionPolicy",
          "Bypass",
          "-Command",
          "irm https://astral.sh/uv/install.ps1 | iex",
        ],
        { stageLabel: "uv-install-packaged-rescue" }
      );
      if (rescue.code === 0) {
        // Ensure common install locations are present on PATH for this
        // process in case the installer placed uv in a user-local bin.
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
        if (commandExists("uv")) {
          log("Packaged Windows: uv installed and found on PATH (rescue succeeded)");
          addManagedBinToPath();
          report(STAGES.ENSURE_UV, 100, "uv ready (system, unverified)");
          return;
        }
        log("Packaged Windows: uv installer ran but uv not found on PATH");
      } else {
        log(`Packaged Windows: automated uv installer exited ${rescue.code}`);
      }
    } catch (rescueErr) {
      log(`Packaged Windows: rescue installer threw: ${rescueErr.message}`);
    }
  }

  // Packaged build, but we somehow don't have a bundled binary for this
  // platform AND no system uv. Last-ditch: accept an unverified system uv
  // if present; otherwise fail with a clear message.
  if (commandExists("uv")) {
    log(
      `No bundled uv for ${process.platform}-${process.arch}, using system uv on PATH (unverified)`
    );
    report(STAGES.ENSURE_UV, 100, "uv ready (system, unverified)");
    return;
  }

  throw new InstallError(
    `GAIA could not find or install its Python helper (uv) required to provision the backend.`,
    {
      stage: STAGES.ENSURE_UV,
      suggestion:
        "GAIA attempted automatic recovery but could not install uv. Please either: (a) click 'Install uv' in the dialog to let GAIA try again, or (b) install uv from https://astral.sh/uv and re-launch GAIA.",
    }
  );
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
  // GAIA_LOCAL_WHEEL: CI-only. When set, install from the given wheel path
  // instead of pulling from PyPI. This breaks the circular dependency in
  // release pipeline smoke tests that run before PyPI publish. The `[ui]`
  // extras marker is preserved so the local install matches the PyPI path
  // (fastapi, uvicorn, python-multipart, httpx, psutil) — otherwise the
  // backend venv comes up missing every UI dep and /api/health never binds.
  const localWheel = process.env.GAIA_LOCAL_WHEEL || null;
  const pipPackage = localWheel
    ? `${localWheel}[ui]`
    : `amd-gaia[ui]==${version}`;
  const skipGaiaInit =
    Boolean(opts.skipGaiaInit) || isTruthyEnv(process.env.GAIA_SKIP_GAIA_INIT);

  log("================================================");
  log("  Installing GAIA backend");
  log("================================================");
  log(`Package: ${pipPackage}`);
  log(`Location: ${GAIA_VENV_DISPLAY}`);

  setState(STATES.INSTALLING, { stage: STAGES.ENSURE_UV, version });

  // Stage 1: ensure uv
  await ensureUv({ onProgress: opts.onProgress, isPackaged: opts.isPackaged });

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
  // Skip when installing from a local wheel — PyPI index not needed.
  if (!IS_WINDOWS && !localWheel) {
    pipArgs.push("--extra-index-url", "https://download.pytorch.org/whl/cpu");
  }

  // Retry the install on transient PyPI/network failures. The heavy
  // transitive deps (scipy, numpy, torch) are fetched live from PyPI even
  // when the gaia wheel itself is local, so a single broken-pipe mid-download
  // would otherwise fail the whole bootstrap (and block a release). File-lock
  // failures (Windows os-error-32) are NOT retried — they need user action.
  let installResult;
  let useNativeTls = false;
  let nativeTlsAttempted = false;
  // The TLS branch flips useNativeTls and `continue`s, consuming an iteration.
  // If the signature only surfaces on the final attempt, extend the loop once
  // so the promised --native-tls retry actually runs (issue #1693 review).
  for (
    let attempt = 1;
    attempt <= INSTALL_MAX_ATTEMPTS || (useNativeTls && !nativeTlsAttempted);
    attempt++
  ) {
    const attemptArgs = useNativeTls ? [...pipArgs, "--native-tls"] : pipArgs;
    if (useNativeTls) nativeTlsAttempted = true;
    installResult = await runCommand("uv", attemptArgs, { stageLabel: "pip" });
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
  if (installResult.code !== 0) {
    // Windows: an upgrade can't replace gaia.exe while a previous GAIA
    // process still holds it open (issue #1388). The orphan cleanup in
    // main.cjs should have killed it first; if the lock persists, point the
    // user at the live process rather than the generic manual-install hint.
    const output = `${installResult.stdout || ""}\n${installResult.stderr || ""}`;
    let suggestion;
    if (isFileLockedError(output)) {
      suggestion =
        "A running GAIA process is locking the install. Close GAIA completely (including any leftover gaia.exe in Task Manager), then relaunch.";
    } else if (isTlsCertError(output)) {
      suggestion =
        "PyPI's certificate isn't trusted on this network — usually a corporate proxy presenting its own root CA. " +
        (nativeTlsAttempted
          ? "GAIA retried with the OS certificate store and it still failed. "
          : "") +
        "Ask IT to install the proxy's root CA in your system's trusted certificate store, then relaunch GAIA. " +
        "See https://amd-gaia.ai/docs/quickstart#cli-install";
    } else {
      suggestion = `Try installing manually:\n  uv pip install ${pipPackage} --python ${
        IS_WINDOWS ? `${GAIA_VENV_DISPLAY}/Scripts/python.exe` : `${GAIA_VENV_DISPLAY}/bin/python`
      }\nThen restart GAIA. See https://amd-gaia.ai/docs/quickstart#cli-install`;
    }
    throw new InstallError(
      `Failed to install ${pipPackage} (pip exit ${installResult.code}).`,
      {
        stage: STAGES.INSTALL_PACKAGE,
        code: installResult.code,
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

  // Stage 4: gaia init
  if (!skipGaiaInit) {
    setState(STATES.INSTALLING, { stage: STAGES.GAIA_INIT, version });
    report(
      STAGES.GAIA_INIT,
      0,
      "Setting up Lemonade Server and downloading models (this can take several minutes)"
    );

    const initResult = await runCommand(
      GAIA_BIN,
      ["init", "--profile", "minimal", "--yes"],
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
    log("Skipping gaia init (skipGaiaInit=true or GAIA_SKIP_GAIA_INIT set)");
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
    if (existingBin) {
      const installedVersion = getInstalledVersion(existingBin);
      if (expectedVersion !== null && installedVersion === expectedVersion) {
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
          suggestion: "Check the log file and try reinstalling. See https://amd-gaia.ai/docs/quickstart",
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
