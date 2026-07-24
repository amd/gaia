// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * auto-updater.cjs — GAIA Agent UI auto-update service.
 *
 * Wraps `electron-updater` against a CONFIGURABLE generic feed (R2-primary —
 * issue #1724). The forward-update channel URL is resolved at runtime from
 * `GAIA_UPDATE_FEED_URL` or `feedUrl` in ~/.gaia/update-config.json, and the
 * app fetches the mutable `latest*.yml` channel pointer from it. When no feed
 * is configured the updater enters a loud `no-channel` state (no silent
 * no-op). The actual R2 publish feed + mutable channel pointer is milestone-52
 * work (#1719); until it lands, point the env var at any generic feed (a local
 * mock works) to exercise the path.
 *
 * Implements §4 Layer 3 + §7 Phase F of docs/plans/desktop-installer.mdx.
 * Issue #1336: in-app rollback to a specific previous release still lists and
 * installs from GitHub Releases (that path is independent of the forward feed
 * and migrates to R2 with #1719).
 *
 * Behavior:
 *   - First check 10 seconds after `init()` is called (typically from
 *     `app.whenReady`)
 *   - Subsequent checks every 4 hours via re-scheduling setTimeout
 *   - Concurrent-check guard so checks never overlap
 *   - Download silently in the background
 *   - On `update-downloaded`, show a native dialog: "Update ready — restart?"
 *   - Renderer integration via IPC channel `gaia:update:status`
 *   - Disabled entirely via GAIA_DISABLE_UPDATE=1 env var (CI / dev / corp)
 *   - Rollback: listReleases() → installVersion(tag) → pin persisted to
 *     ~/.gaia/update-config.json so auto-update stays paused until resumeUpdates()
 *
 * Exports:
 *   - init(mainWindow)        → set up handlers, schedule checks
 *   - destroy()               → tear down timers and IPC handlers
 *   - checkForUpdates()       → manually trigger a check
 *   - getState()              → returns a copy of the current state
 *   - listReleases()          → fetch available releases from GitHub
 *   - installVersion(tag)     → downgrade/install a specific tagged release
 *   - clearPin()              → resume auto-updates (clear the version pin)
 *   - STATES                  → string constants for valid states
 *
 * Design note: `electron` and `electron-updater` are lazy-required inside
 * `init()`. Accessing `electronUpdater.autoUpdater` outside of an Electron
 * runtime throws synchronously (it reads `app.getVersion()` eagerly), so
 * we keep the module load pure. This also makes `GAIA_DISABLE_UPDATE=1`
 * safely short-circuit before touching any Electron APIs — useful in tests
 * and in environments where the Electron app isn't wired up.
 */

"use strict";

const path = require("path");
const fs = require("fs");
const os = require("os");

// ── Constants ────────────────────────────────────────────────────────────────

const CHECK_DELAY_MS = 10 * 1000; // First check 10s after init
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000; // Subsequent checks every 4h
const LOG_PATH = path.join(os.homedir(), ".gaia", "electron-updater.log");
const UPDATE_CONFIG_PATH = path.join(os.homedir(), ".gaia", "update-config.json");

// Mutable channel pointer name. electron-updater's generic provider fetches
// `${feedUrl}/${DEFAULT_CHANNEL}.yml` (+ per-platform variants). The Hub Worker
// re-points this file each release while versioned artifacts stay immutable —
// that split is the A6/A7 resolution (#1724).
const DEFAULT_CHANNEL = "latest";

// per_page=100 is the GitHub API max for a single page; we fetch broadly so
// draft/prerelease/platform filtering still yields a full page of installable
// releases before the display cap below. >100 releases would need pagination
// (follow the Link rel="next" header). Not built yet.
const GITHUB_API_RELEASES =
  "https://api.github.com/repos/amd/gaia/releases?per_page=100";

// The picker shows only the N most-recent installable releases (rolling back
// more than a few versions is rare). Older versions stay reachable via the
// "browse all on GitHub" link in the picker, so this display cap never makes a
// release unreachable.
const MAX_RELEASES_SHOWN = 10;

const STATES = Object.freeze({
  IDLE: "idle",
  CHECKING: "checking",
  AVAILABLE: "available",
  DOWNLOADING: "downloading",
  DOWNLOADED: "downloaded",
  ERROR: "error",
  DISABLED: "disabled",
  // No forward-update feed configured — loud, actionable, never a silent no-op.
  NO_CHANNEL: "no-channel",
});

// ── Module state ─────────────────────────────────────────────────────────────

/** Shape broadcast to the renderer via `gaia:update:status`. */
const state = {
  status: STATES.IDLE,
  version: null,
  progress: 0,
  releaseNotes: null,
  error: null,
  currentVersion: null,
  pinnedVersion: null,
};

let mainWindowRef = null;
let checkInProgress = false;
let scheduledTimeout = null;
let initialCheckTimeout = null;
let ipcHandlersRegistered = false;
let initialized = false;
// The resolved forward-update feed URL (generic/R2), or null when unconfigured.
let forwardFeedUrl = null;

// Lazy-loaded Electron references (populated inside init()).
let electronApi = null; // { dialog, ipcMain, app }
let autoUpdaterRef = null; // electron-updater's singleton

// ── Logging ──────────────────────────────────────────────────────────────────

function log(level, message, ...args) {
  const ts = new Date().toISOString();
  const extra = args.length ? " " + safeStringify(args) : "";
  const line = `[${ts}] [${level}] ${message}${extra}\n`;
  try {
    fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
    fs.appendFileSync(LOG_PATH, line);
  } catch {
    // Non-fatal — logging must never crash the app.
  }
  try {
    // eslint-disable-next-line no-console
    console.log(`[auto-updater] ${level} ${message}`, ...args);
  } catch {
    // ignore
  }
}

function safeStringify(value) {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ── Pin persistence ───────────────────────────────────────────────────────────

function _loadPin() {
  try {
    if (!fs.existsSync(UPDATE_CONFIG_PATH)) return null;
    const raw = fs.readFileSync(UPDATE_CONFIG_PATH, "utf8");
    const cfg = JSON.parse(raw);
    return cfg.pinnedVersion || null;
  } catch (err) {
    log("warn", "Failed to read version pin — treating as unpinned:", err && err.message);
    return null;
  }
}

/**
 * Persist the version pin. Propagates write errors to the caller so that a
 * failed pin write can abort a rollback (AC2 must not be silently broken).
 * Callers that can tolerate a failed write (e.g. clearPin) should catch.
 */
function _savePin(pinnedVersion) {
  fs.mkdirSync(path.dirname(UPDATE_CONFIG_PATH), { recursive: true });
  // Merge into any existing config so we don't clobber a persisted feedUrl.
  let existing = {};
  try {
    if (fs.existsSync(UPDATE_CONFIG_PATH)) {
      existing = JSON.parse(fs.readFileSync(UPDATE_CONFIG_PATH, "utf8")) || {};
    }
  } catch {
    existing = {};
  }
  fs.writeFileSync(
    UPDATE_CONFIG_PATH,
    JSON.stringify({ ...existing, pinnedVersion }, null, 2),
    "utf8"
  );
}

// ── Forward-update feed resolution (issue #1724) ────────────────────────────────

/**
 * Resolve the generic forward-update feed URL, or null if none is configured.
 * Precedence: GAIA_UPDATE_FEED_URL env > feedUrl in update-config.json.
 */
function _resolveFeedUrl() {
  const envUrl = process.env.GAIA_UPDATE_FEED_URL;
  if (typeof envUrl === "string" && envUrl.trim()) return envUrl.trim();
  try {
    if (fs.existsSync(UPDATE_CONFIG_PATH)) {
      const cfg = JSON.parse(fs.readFileSync(UPDATE_CONFIG_PATH, "utf8"));
      if (cfg && typeof cfg.feedUrl === "string" && cfg.feedUrl.trim()) {
        return cfg.feedUrl.trim();
      }
    }
  } catch (err) {
    log("warn", "Failed to read feedUrl from update-config.json:", err && err.message);
  }
  return null;
}

/**
 * Point electron-updater at the configured generic (R2) feed + mutable channel.
 * When nothing is configured, enter the loud NO_CHANNEL state and set no feed —
 * an unconfigured updater must never silently pretend it's up to date.
 *
 * @returns {boolean} true when a feed was applied, false when NO_CHANNEL.
 */
function _applyForwardFeed() {
  if (!autoUpdaterRef) return false;
  const url = _resolveFeedUrl();
  if (!url) {
    forwardFeedUrl = null;
    setState({
      status: STATES.NO_CHANNEL,
      error:
        "No update channel configured — set GAIA_UPDATE_FEED_URL (or feedUrl " +
        "in ~/.gaia/update-config.json) to your R2 channel base URL. " +
        "Auto-update is paused until a feed is configured.",
    });
    log("warn", "No update feed configured — auto-update paused (NO_CHANNEL)");
    return false;
  }
  try {
    autoUpdaterRef.setFeedURL({
      provider: "generic",
      url,
      channel: DEFAULT_CHANNEL,
    });
    forwardFeedUrl = url;
    log("info", `Update feed set (generic): ${url} [channel=${DEFAULT_CHANNEL}]`);
    return true;
  } catch (err) {
    forwardFeedUrl = null;
    setState({
      status: STATES.ERROR,
      error: `Failed to set update feed URL: ${(err && err.message) || String(err)}`,
    });
    log("error", "Failed to set feed URL:", err && err.message);
    return false;
  }
}

// ── State management ─────────────────────────────────────────────────────────

function broadcastState() {
  if (!mainWindowRef) return;
  try {
    if (mainWindowRef.isDestroyed && mainWindowRef.isDestroyed()) return;
    if (mainWindowRef.webContents && !mainWindowRef.webContents.isDestroyed()) {
      mainWindowRef.webContents.send("gaia:update:status", { ...state });
    }
  } catch (err) {
    log("warn", "Failed to broadcast state:", err && err.message);
  }
}

function setState(patch) {
  Object.assign(state, patch);
  broadcastState();
}

function getState() {
  return { ...state };
}

// ── Env gate ─────────────────────────────────────────────────────────────────

function isDisabled() {
  return process.env.GAIA_DISABLE_UPDATE === "1";
}

// ── Platform asset detection ─────────────────────────────────────────────────

function _platformAssetPattern() {
  if (process.platform === "win32") return /setup\.exe$/i;
  if (process.platform === "darwin") return /\.zip$/i;
  // Linux: AppImage (deb is apt-managed, electron-updater can't downgrade it)
  return /\.AppImage$/i;
}

function _releaseHasPlatformAsset(release) {
  const pattern = _platformAssetPattern();
  return (
    Array.isArray(release.assets) &&
    release.assets.some((a) => a && a.name && pattern.test(a.name))
  );
}

// ── listReleases ──────────────────────────────────────────────────────────────

/**
 * Fetch published GitHub releases filtered for this platform.
 *
 * Returns an array of ReleaseInfo objects newest-first, or a plain object
 * { error: string } if the network call fails — never an empty array that
 * would silently mask failures.
 *
 * @returns {Promise<Array<ReleaseInfo> | { error: string }>}
 */
async function listReleases() {
  const allowPrerelease = process.env.GAIA_UPDATE_PRERELEASE === "1";
  const pinnedVersion = _loadPin();

  // Get the running version — use the cached electronApi if available, else
  // attempt a direct require (works when called after init(), and from tests
  // where electron is mocked via moduleNameMapper).
  let currentVersion = null;
  if (electronApi && electronApi.app) {
    currentVersion = electronApi.app.getVersion() || null;
  } else {
    try {
      // eslint-disable-next-line global-require
      const electron = require("electron");
      if (electron && electron.app && electron.app.getVersion) {
        currentVersion = electron.app.getVersion() || null;
      }
    } catch {
      // ignore — currentVersion stays null
    }
  }

  let releases;
  try {
    const resp = await fetch(GITHUB_API_RELEASES, {
      headers: {
        Accept: "application/vnd.github+json",
        "User-Agent": "GAIA-Agent-UI",
      },
    });
    if (!resp.ok) {
      return {
        error: `Couldn't reach GitHub to list releases (HTTP ${resp.status}) — check your connection; you can still download installers from the Releases page.`,
      };
    }
    releases = await resp.json();
  } catch (err) {
    return {
      error: `Couldn't reach GitHub to list releases — check your connection; you can still download installers from the Releases page. (${
        err && err.message
      })`,
    };
  }

  const result = releases
    .filter(
      (r) =>
        !r.draft &&
        (allowPrerelease || !r.prerelease) &&
        _releaseHasPlatformAsset(r)
    )
    .map((r) => {
      const tag = r.tag_name || "";
      // Version is tag without leading 'v'
      const version = tag.startsWith("v") ? tag.slice(1) : tag;
      return {
        version,
        tag,
        date: r.published_at || null,
        notesUrl: r.html_url || null,
        isCurrent: currentVersion ? version === currentVersion : false,
        isPinned: pinnedVersion ? tag === pinnedVersion : false,
      };
    });

  // Cap to the most-recent installable releases; older ones remain reachable
  // via the "browse all on GitHub" link in the picker.
  return result.slice(0, MAX_RELEASES_SHOWN);
}

// ── installVersion ────────────────────────────────────────────────────────────

/**
 * Download and install a specific tagged release, then prompt restart.
 *
 * Uses the "generic provider + per-release channel file" pattern:
 * each GitHub release hosts its own latest*.yml which describes that exact
 * version. Setting allowDowngrade=true + pointing setFeedURL at the release
 * folder lets electron-updater treat it as an available update.
 *
 * Persists the version pin BEFORE starting the download so that even if the
 * user dismisses the restart dialog, the pin is already in place for AC2.
 *
 * @param {string} tag  The git tag, e.g. "v0.20.0"
 */
const TAG_RE = /^v\d+\.\d+\.\d+(-[A-Za-z0-9.]+)?$/;

async function installVersion(tag) {
  if (!autoUpdaterRef) {
    throw new Error("installVersion called before auto-updater was initialised");
  }
  if (typeof tag !== "string" || !TAG_RE.test(tag)) {
    throw new Error(
      `Invalid release tag "${tag}" — expected the form "vMAJOR.MINOR.PATCH" (e.g. "v0.20.0")`
    );
  }
  if (checkInProgress) {
    throw new Error(
      "An update check is already in progress — try again in a moment."
    );
  }

  log("info", `Installing version ${tag} via targeted rollback`);

  // Persist pin before touching the feed/allowDowngrade so AC2 holds even if
  // the download completes but the user dismisses the restart prompt. If the
  // pin can't be persisted we ABORT — proceeding would let the next launch
  // silently auto-upgrade past the rollback (no silent fallbacks).
  try {
    _savePin(tag);
  } catch (err) {
    const msg = (err && err.message) || String(err);
    throw new Error(
      `Failed to persist version pin — rollback aborted: ${msg}`
    );
  }
  setState({ pinnedVersion: tag });

  checkInProgress = true;
  try {
    autoUpdaterRef.allowDowngrade = true;
    autoUpdaterRef.setFeedURL({
      provider: "generic",
      url: `https://github.com/amd/gaia/releases/download/${tag}/`,
    });

    // Known limitation: the generic/tagged feed + allowDowngrade stay set
    // until clearPin(). While pinned, autoDownload=false (set at init) keeps
    // the scheduled check from installing anything, but that check queries the
    // pinned release's own feed rather than GitHub-latest — so the "newer
    // version available, you're pinned" chip won't fire until the user resumes.
    // Restoring the github feed in the scheduled path risks racing the still-
    // in-flight rollback download (which outlives this checkInProgress window),
    // so it's intentionally deferred.

    // Kick off the targeted check — will fire update-available → downloads →
    // update-downloaded → our existing dialog prompts restart.
    await autoUpdaterRef.checkForUpdates();
  } finally {
    checkInProgress = false;
  }
}

// ── clearPin / resumeUpdates ──────────────────────────────────────────────────

/**
 * Clear the version pin and resume normal auto-updates.
 *
 * Restores the configured forward (R2 generic) feed, sets autoDownload=true,
 * and clears allowDowngrade so the next check is the normal forward-only flow.
 * If no feed is configured, resume lands in the loud NO_CHANNEL state rather
 * than silently reverting to a stale rollback feed.
 */
function clearPin() {
  // A failed clear is non-fatal — worst case the stale pin re-pauses updates
  // on next launch and the user can retry Resume. Don't block resume on it.
  try {
    _savePin(null);
  } catch (err) {
    log("warn", "Failed to clear pin file:", err && err.message);
  }
  setState({ pinnedVersion: null });

  if (autoUpdaterRef) {
    autoUpdaterRef.autoDownload = true;
    autoUpdaterRef.allowDowngrade = false;
    // Restore the forward channel (overrides any generic/tagged rollback feed
    // set by installVersion). Loud NO_CHANNEL if unconfigured.
    _applyForwardFeed();
  }

  log("info", "Version pin cleared — auto-updates resumed");
}

// ── Core check ───────────────────────────────────────────────────────────────

async function checkForUpdates() {
  if (isDisabled()) {
    setState({ status: STATES.DISABLED });
    log("info", "Update check skipped — GAIA_DISABLE_UPDATE=1");
    return;
  }
  if (!autoUpdaterRef) {
    log("warn", "checkForUpdates called before init — ignoring");
    return;
  }
  // No forward feed and not pinned → surface the loud NO_CHANNEL state instead
  // of asking electron-updater to check a feed that doesn't exist. Re-resolve
  // first so a feed written to config after init() is picked up here.
  if (!forwardFeedUrl && !state.pinnedVersion) {
    if (!_applyForwardFeed()) return;
    // Feed just became available after a no-feed (NO_CHANNEL) start — init()
    // skipped arming the periodic scheduler, so arm it now or background checks
    // never resume once the feed goes live.
    if (!scheduledTimeout) scheduleNextCheck();
  }
  if (checkInProgress) {
    log("info", "Skipping check — another check is already in progress");
    return;
  }
  checkInProgress = true;
  setState({ status: STATES.CHECKING, error: null });
  try {
    log("info", "Checking for updates...");
    await autoUpdaterRef.checkForUpdates();
  } catch (err) {
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || String(err),
    });
    log("error", "Update check failed:", err && err.message);
  } finally {
    checkInProgress = false;
  }
}

function scheduleNextCheck() {
  if (scheduledTimeout) {
    clearTimeout(scheduledTimeout);
    scheduledTimeout = null;
  }
  scheduledTimeout = setTimeout(async () => {
    try {
      await checkForUpdates();
    } catch (err) {
      log("error", "Scheduled check threw:", err && err.message);
    }
    scheduleNextCheck();
  }, CHECK_INTERVAL_MS);
}

// ── Event wiring ─────────────────────────────────────────────────────────────

function wireAutoUpdaterEvents() {
  autoUpdaterRef.on("checking-for-update", () => {
    setState({ status: STATES.CHECKING });
  });

  autoUpdaterRef.on("update-available", (info) => {
    const releaseNotes =
      typeof info.releaseNotes === "string" ? info.releaseNotes : null;
    setState({
      status: STATES.AVAILABLE,
      version: info.version || null,
      releaseNotes,
      error: null,
    });
    log("info", `Update available: ${info.version}`);
  });

  autoUpdaterRef.on("update-not-available", (info) => {
    // Reset to idle so the UI hides any stale "available" chip.
    setState({
      status: STATES.IDLE,
      version: null,
      progress: 0,
      releaseNotes: null,
      error: null,
    });
    log("info", `No update available (current ${info && info.version})`);
  });

  autoUpdaterRef.on("download-progress", (progress) => {
    const percent =
      progress && typeof progress.percent === "number"
        ? Math.max(0, Math.min(100, Math.round(progress.percent)))
        : 0;
    setState({
      status: STATES.DOWNLOADING,
      progress: percent,
    });
  });

  autoUpdaterRef.on("update-downloaded", async (info) => {
    setState({
      status: STATES.DOWNLOADED,
      version: (info && info.version) || state.version,
      progress: 100,
      error: null,
    });
    log("info", `Update downloaded: ${info && info.version}`);

    if (!electronApi || !electronApi.dialog) {
      log("warn", "No dialog available — skipping restart prompt");
      return;
    }
    try {
      const isRollback =
        state.pinnedVersion !== null &&
        info &&
        info.version;
      const title = isRollback ? "Roll back ready" : "Update ready";
      const message = isRollback
        ? `GAIA ${info.version} is ready to install.`
        : `GAIA ${info && info.version ? info.version : ""} has been downloaded.`;
      const detail = isRollback
        ? `The app will restart into v${info.version}. Your chat history will be preserved.`
        : "Restart the app to apply the update. Your chat history will be preserved.";

      const choice = await electronApi.dialog.showMessageBox(
        mainWindowRef && !mainWindowRef.isDestroyed() ? mainWindowRef : null,
        {
          type: "info",
          buttons: ["Restart now", "Later"],
          defaultId: 0,
          cancelId: 1,
          title,
          message,
          detail,
        }
      );
      if (choice && choice.response === 0) {
        log("info", "User chose to restart — calling quitAndInstall");
        autoUpdaterRef.quitAndInstall(false, true);
      } else {
        log("info", "User deferred restart — will install on next quit");
      }
    } catch (err) {
      log("error", "Failed to show restart dialog:", err && err.message);
    }
  });

  autoUpdaterRef.on("error", (err) => {
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || String(err),
    });
    log("error", "electron-updater error:", err && err.message);
  });
}

function registerIpcHandlers() {
  if (ipcHandlersRegistered || !electronApi || !electronApi.ipcMain) return;
  const { ipcMain } = electronApi;

  ipcMain.handle("gaia:update:get-status", () => getState());
  ipcMain.handle("gaia:update:check", async () => {
    await checkForUpdates();
    return getState();
  });
  ipcMain.handle("gaia:update:list-releases", async () => {
    return listReleases();
  });
  ipcMain.handle("gaia:update:install-version", async (_event, tag) => {
    await installVersion(tag);
    return getState();
  });
  ipcMain.handle("gaia:update:resume", async () => {
    clearPin();
    return getState();
  });

  ipcHandlersRegistered = true;
}

function unregisterIpcHandlers() {
  if (!ipcHandlersRegistered || !electronApi || !electronApi.ipcMain) return;
  const { ipcMain } = electronApi;
  const channels = [
    "gaia:update:get-status",
    "gaia:update:check",
    "gaia:update:list-releases",
    "gaia:update:install-version",
    "gaia:update:resume",
  ];
  for (const ch of channels) {
    try {
      ipcMain.removeHandler(ch);
    } catch {
      // ignore
    }
  }
  ipcHandlersRegistered = false;
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Initialize the auto-updater. Safe to call multiple times — subsequent
 * calls update the window reference only.
 *
 * Must NOT block app launch: if anything goes wrong the caller catches and
 * continues, and this function itself never throws.
 *
 * @param {Electron.BrowserWindow | null} mainWindow
 */
function init(mainWindow) {
  mainWindowRef = mainWindow || null;

  // Disabled path — short-circuit BEFORE touching any Electron APIs so
  // this works in plain Node tests where require('electron') returns a
  // string and the electron-updater singleton throws on access.
  if (isDisabled()) {
    setState({ status: STATES.DISABLED });
    log("info", "Auto-updater disabled via GAIA_DISABLE_UPDATE=1");
    return;
  }

  if (initialized) {
    // Just refresh the window reference and push the current state down.
    broadcastState();
    return;
  }

  // Read the persisted pin before touching electron-updater so pin gating
  // is applied to the very first autoDownload flag set below.
  const savedPin = _loadPin();
  if (savedPin) {
    state.pinnedVersion = savedPin;
    log("info", `Found persisted version pin: ${savedPin} — autoDownload will be paused`);
  }

  // Lazy-load Electron and electron-updater. Any failure here is logged
  // and the updater stays in `idle` — we never crash the app.
  try {
    // eslint-disable-next-line global-require
    const electron = require("electron");
    // eslint-disable-next-line global-require
    const electronUpdater = require("electron-updater");

    if (!electron || !electron.app || !electron.ipcMain || !electron.dialog) {
      log(
        "warn",
        "Electron APIs unavailable — auto-updater will not be active"
      );
      return;
    }

    electronApi = {
      dialog: electron.dialog,
      ipcMain: electron.ipcMain,
      app: electron.app,
    };
    autoUpdaterRef = electronUpdater.autoUpdater;

    // Record the running version for getState() / listReleases().
    try {
      state.currentVersion = electron.app.getVersion() || null;
    } catch {
      // ignore
    }
  } catch (err) {
    log("error", "Failed to load electron-updater:", err && err.message);
    setState({
      status: STATES.ERROR,
      error: (err && err.message) || "Failed to load electron-updater",
    });
    return;
  }

  // Configure electron-updater.
  try {
    // When pinned, suppress autoDownload so the scheduled forward-only check
    // cannot silently download/install a newer version (AC2).
    autoUpdaterRef.autoDownload = savedPin ? false : true;
    autoUpdaterRef.autoInstallOnAppQuit = true;
    autoUpdaterRef.disableWebInstaller = true;
    autoUpdaterRef.allowDowngrade = false;
    autoUpdaterRef.allowPrerelease =
      process.env.GAIA_UPDATE_PRERELEASE === "1";

    autoUpdaterRef.logger = {
      info: (m) => log("info", String(m)),
      warn: (m) => log("warn", String(m)),
      error: (m) => log("error", String(m)),
      debug: (m) => log("debug", String(m)),
    };
  } catch (err) {
    log("warn", "Failed to configure autoUpdater flags:", err && err.message);
  }

  try {
    wireAutoUpdaterEvents();
  } catch (err) {
    log("error", "Failed to wire autoUpdater events:", err && err.message);
    return;
  }

  try {
    registerIpcHandlers();
  } catch (err) {
    log("warn", "Failed to register IPC handlers:", err && err.message);
  }

  // Point the updater at the configured R2 generic feed (#1724). When a pin is
  // active, installVersion() owns the feed (rollback path), so skip. With no
  // pin and no feed, _applyForwardFeed enters the loud NO_CHANNEL state and we
  // don't schedule checks against a nonexistent feed.
  let feedReady = true;
  if (!savedPin) {
    feedReady = _applyForwardFeed();
  }

  initialized = true;

  if (!feedReady && !savedPin) {
    log(
      "info",
      "Auto-updater initialized without a channel — checks paused until a feed is configured"
    );
    return;
  }

  // First check after CHECK_DELAY_MS, then every CHECK_INTERVAL_MS.
  initialCheckTimeout = setTimeout(async () => {
    try {
      await checkForUpdates();
    } catch (err) {
      log("error", "Initial check threw:", err && err.message);
    }
    scheduleNextCheck();
  }, CHECK_DELAY_MS);

  log(
    "info",
    `Auto-updater initialized; first check in ${CHECK_DELAY_MS}ms`
  );
}

/** Tear down timers and IPC handlers. Safe to call multiple times. */
function destroy() {
  if (initialCheckTimeout) {
    clearTimeout(initialCheckTimeout);
    initialCheckTimeout = null;
  }
  if (scheduledTimeout) {
    clearTimeout(scheduledTimeout);
    scheduledTimeout = null;
  }
  unregisterIpcHandlers();
  mainWindowRef = null;
  // Keep `initialized` true — calling init() again after destroy() is not a
  // supported lifecycle and we'd need to re-wire electron-updater events
  // which cannot be reliably cleaned up via its public API.
}

module.exports = {
  init,
  destroy,
  checkForUpdates,
  getState,
  listReleases,
  installVersion,
  clearPin,
  STATES,
};
