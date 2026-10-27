// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * auto-updater.cjs — GAIA Agent UI auto-update service.
 *
 * Wraps `electron-updater` against a generic feed. A feed set in
 * `GAIA_UPDATE_FEED_URL` or `feedUrl` in ~/.gaia/update-config.json (the R2
 * channel of #1724) wins. Otherwise each check reads the GitHub releases and
 * points the feed at the newest one on this install's channel that publishes
 * this platform's `latest*.yml`; when none does, the updater enters the
 * `no-channel` state and says so (no silent "up to date").
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
 *   - Auto-update is OFF by default (`autoUpdate` in ~/.gaia/update-config.json,
 *     shared with the Terminal UI). Off, a found update is only offered: the
 *     renderer's "Update now" calls downloadUpdate(). On, it downloads silently.
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
 *   - setAutoUpdate(enabled)  → persist the auto-update preference
 *   - downloadUpdate()        → download the offered update ("Update now")
 *   - restartToInstall()      → restart into a downloaded update
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

const RELEASES_PAGE = "https://github.com/amd/gaia/releases";

const STATES = Object.freeze({
  IDLE: "idle",
  CHECKING: "checking",
  AVAILABLE: "available",
  DOWNLOADING: "downloading",
  DOWNLOADED: "downloaded",
  ERROR: "error",
  DISABLED: "disabled",
  // No release publishes a desktop update for this platform — said in
  // Settings, never shown as "up to date".
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
  autoUpdate: false,
  // Offer release candidates too; off by default. A release-candidate build
  // is offered newer candidates without it.
  includePrereleases: false,
  // update-config.json could not be read; kept apart from `error` so a later
  // check's status never hides it.
  configError: null,
};

let mainWindowRef = null;
/** Asks before an update restart would cut off running work (set by init). */
let confirmRestartRef = null;
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
 * Off unless the user turned it on: the app still changes fast. An unreadable
 * file is reported (Settings shows it), and auto-update stays off until fixed.
 */
function _loadAutoUpdate() {
  if (!fs.existsSync(UPDATE_CONFIG_PATH)) {
    return { autoUpdate: false, includePrereleases: false, error: null };
  }
  try {
    const cfg = JSON.parse(fs.readFileSync(UPDATE_CONFIG_PATH, "utf8"));
    return {
      autoUpdate: Boolean(cfg && cfg.autoUpdate === true),
      includePrereleases: Boolean(cfg && cfg.includePrereleases === true),
      error: null,
    };
  } catch (err) {
    const error =
      `${UPDATE_CONFIG_PATH} cannot be read (${(err && err.message) || err}), so auto-update is off — ` +
      "fix the file by hand or delete it.";
    log("error", error);
    return { autoUpdate: false, includePrereleases: false, error };
  }
}

/** "0.25.2-rc.3" → true. A candidate build keeps being offered candidates. */
function _isPrereleaseVersion(version) {
  return typeof version === "string" && /^\d+\.\d+\.\d+-/.test(version);
}

/**
 * Whether release candidates are offered: the user opted in, this build is
 * itself a candidate, or GAIA_UPDATE_PRERELEASE=1. A channel, not an install
 * trigger: with auto-update off an offered candidate still waits to be asked.
 */
function _prereleasesAllowed() {
  return (
    process.env.GAIA_UPDATE_PRERELEASE === "1" ||
    state.includePrereleases ||
    _isPrereleaseVersion(state.currentVersion)
  );
}

function _savePin(pinnedVersion) {
  _writeConfig({ pinnedVersion });
}

/**
 * Merge `patch` into update-config.json. Propagates write errors so a failed
 * pin write can abort a rollback (AC2 must not be silently broken). Callers
 * that can tolerate a failed write (e.g. clearPin) should catch.
 */
function _writeConfig(patch) {
  fs.mkdirSync(path.dirname(UPDATE_CONFIG_PATH), { recursive: true });
  // Merge so we don't clobber a persisted feedUrl or the Terminal UI's keys.
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
    JSON.stringify({ ...existing, ...patch }, null, 2),
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
 * With nothing configured, each check instead reads the newest GitHub release
 * on this install's channel (_applyGithubFeed) — where releases publish their
 * latest*.yml today.
 *
 * @returns {boolean} true when a feed is ready, false when setting it failed.
 */
function _applyForwardFeed() {
  if (!autoUpdaterRef) return false;
  const url = _resolveFeedUrl();
  if (!url) {
    forwardFeedUrl = null;
    log("info", "No update feed configured — checks read GitHub releases");
    return true;
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

/** -1, 0 or 1 comparing "0.25.2-rc.3"-style versions; candidates sort before their final. */
function _compareVersions(a, b) {
  const parse = (v) => {
    const m = /^v?(\d+)\.(\d+)\.(\d+)(?:-rc\.?(\d+))?$/.exec(String(v || ""));
    if (!m) return null;
    return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] === undefined ? Infinity : Number(m[4])];
  };
  const x = parse(a);
  const y = parse(b);
  if (!x || !y) return NaN;
  for (let i = 0; i < 4; i++) {
    if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1;
  }
  return 0;
}

/** The update manifest electron-updater reads on this platform. */
function _platformManifest() {
  if (process.platform === "win32") return "latest.yml";
  if (process.platform === "darwin") return "latest-mac.yml";
  return "latest-linux.yml";
}

/**
 * Point the feed at the newest GitHub release on this install's channel that
 * carries this platform's installer and update manifest; returns its tag.
 * Throws an actionable error when GitHub can't be read. Returns null, in the
 * NO_CHANNEL state, when no release has an update for this platform.
 */
async function _applyGithubFeed() {
  const allowPrerelease = _prereleasesAllowed();
  let releases;
  try {
    const resp = await fetch(GITHUB_API_RELEASES, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "GAIA-Agent-UI" },
    });
    if (!resp.ok) {
      const limited = resp.status === 403 || resp.status === 429;
      throw new Error(
        `GitHub answered HTTP ${resp.status} when asked for GAIA releases` +
          (limited ? " — its hourly limit for this network may be used up; try again later" : " — try again later") +
          `, or download an installer from ${RELEASES_PAGE}.`
      );
    }
    releases = await resp.json();
  } catch (err) {
    if (err && /^GitHub answered/.test(err.message)) throw err;
    throw new Error(
      `Couldn't reach GitHub to check for updates (${(err && err.message) || err}) — check your connection, ` +
        `or download an installer from ${RELEASES_PAGE}.`
    );
  }
  if (!Array.isArray(releases)) {
    throw new Error(`GitHub's release list was not a list — try again later, or see ${RELEASES_PAGE}.`);
  }
  const manifest = _platformManifest();
  const candidates = releases.filter(
    (r) =>
      r &&
      !r.draft &&
      TAG_RE.test(r.tag_name || "") &&
      !Number.isNaN(_compareVersions(r.tag_name, "0.0.0")) &&
      (allowPrerelease || !r.prerelease) &&
      _releaseHasPlatformAsset(r) &&
      Array.isArray(r.assets) &&
      r.assets.some((a) => a && a.name === manifest)
  );
  if (candidates.length === 0) {
    const error =
      `No GAIA release publishes a desktop update for this platform yet (it needs ${manifest} ` +
      `and its installer), so GAIA can't update itself here — download an installer from ${RELEASES_PAGE}.`;
    setState({ status: STATES.NO_CHANNEL, error });
    log("warn", error);
    return null;
  }
  candidates.sort((a, b) => _compareVersions(b.tag_name, a.tag_name));
  const tag = candidates[0].tag_name;
  autoUpdaterRef.setFeedURL({
    provider: "generic",
    url: `https://github.com/amd/gaia/releases/download/${tag}/`,
    channel: DEFAULT_CHANNEL,
  });
  log("info", `Update feed set from GitHub: ${tag}`);
  return tag;
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
  const allowPrerelease = _prereleasesAllowed();
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
  const previousPin = state.pinnedVersion;
  try {
    _savePin(tag);
  } catch (err) {
    const msg = (err && err.message) || String(err);
    throw new Error(
      `Failed to persist version pin — rollback aborted: ${msg}`
    );
  }
  setState({ pinnedVersion: tag });
  // A pin always stops background downloads; this one is downloaded below.
  autoUpdaterRef.autoDownload = false;

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

    // Targeted check → update-available. autoDownload is off (default, and
    // always while pinned), so download explicitly; update-downloaded then
    // prompts the restart.
    await autoUpdaterRef.checkForUpdates();
    if (state.status !== STATES.AVAILABLE) {
      throw new Error(
        `GAIA ${tag} has no installer for this platform at its release feed` +
          (state.error ? `: ${state.error}` : ".") +
          " Download it from https://github.com/amd/gaia/releases instead."
      );
    }
    setState({ status: STATES.DOWNLOADING, progress: 0, error: null });
    await autoUpdaterRef.downloadUpdate();
  } catch (err) {
    // Don't leave the app pinned to a version that never installed.
    const pinNote = _restorePinAfterFailedInstall(previousPin, tag);
    const msg = (err && err.message) || String(err);
    setState({ status: STATES.ERROR, error: msg });
    throw new Error(`${msg} ${pinNote}`);
  } finally {
    checkInProgress = false;
  }
}

/** Undo the pin a failed install wrote; returns the sentence saying where things stand. */
function _restorePinAfterFailedInstall(previousPin, failedTag) {
  try {
    _savePin(previousPin || null);
  } catch (err) {
    // The file still pins failedTag; say so rather than claim otherwise.
    log("error", "Failed to restore the pin after a failed install:", err && err.message);
    return (
      `The pin to ${failedTag} could not be undone in ${UPDATE_CONFIG_PATH} ` +
      `(${(err && err.message) || err}) — fix that file, then use Unpin in Settings → General → Updates.`
    );
  }
  if (!previousPin) {
    clearPin();
    return "Updates are not paused.";
  }
  setState({ pinnedVersion: previousPin });
  autoUpdaterRef.setFeedURL({
    provider: "generic",
    url: `https://github.com/amd/gaia/releases/download/${previousPin}/`,
  });
  return `Still pinned to ${previousPin}.`;
}

// ── clearPin / resumeUpdates ──────────────────────────────────────────────────

/**
 * Clear the version pin and resume normal auto-updates.
 *
 * Restores the configured forward (R2 generic) feed, sets autoDownload=true,
 * and clears allowDowngrade so the next check is the normal forward-only flow.
 * With no feed configured, the next check reads GitHub releases again rather
 * than staying on the rollback feed.
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
    autoUpdaterRef.autoDownload = state.autoUpdate;
    autoUpdaterRef.allowDowngrade = false;
    // Restore the forward channel (overrides any generic/tagged rollback feed
    // set by installVersion); unconfigured means GitHub releases.
    _applyForwardFeed();
  }

  log("info", "Version pin cleared — auto-updates resumed");
}

// ── Auto-update preference / user-driven install ─────────────────────────────

/**
 * Persist the auto-update preference. Throws when it can't be saved, so the
 * Settings toggle never shows a value the next launch won't honour.
 */
async function setAutoUpdate(enabled) {
  if (typeof enabled !== "boolean") {
    throw new TypeError(`setAutoUpdate expects true or false, got ${typeof enabled}`);
  }
  try {
    _writeConfig({ autoUpdate: enabled });
  } catch (err) {
    throw new Error(
      `Couldn't save the auto-update setting to ${UPDATE_CONFIG_PATH}: ${(err && err.message) || err}`
    );
  }
  setState({ autoUpdate: enabled });
  if (autoUpdaterRef) {
    autoUpdaterRef.autoDownload = enabled && !state.pinnedVersion;
    autoUpdaterRef.allowPrerelease = _prereleasesAllowed();
  }
  log("info", `Auto-update ${enabled ? "enabled" : "disabled"}`);
  // Turning it on with an update already offered means "install it".
  if (enabled && !state.pinnedVersion && state.status === STATES.AVAILABLE) {
    await downloadUpdate();
  }
}

/**
 * Persist whether release candidates are offered as updates. Independent of
 * auto-update: it changes what is offered, not whether it installs unasked.
 */
async function setIncludePrereleases(enabled) {
  if (typeof enabled !== "boolean") {
    throw new TypeError(`setIncludePrereleases expects true or false, got ${typeof enabled}`);
  }
  try {
    _writeConfig({ includePrereleases: enabled });
  } catch (err) {
    throw new Error(
      `Couldn't save the release-candidate setting to ${UPDATE_CONFIG_PATH}: ${(err && err.message) || err}`
    );
  }
  setState({ includePrereleases: enabled });
  if (autoUpdaterRef) {
    autoUpdaterRef.allowPrerelease = _prereleasesAllowed();
  }
  log("info", `Release-candidate updates ${enabled ? "enabled" : "disabled"}`);
}

/** "Update now": download the update a check found; the restart dialog follows. */
async function downloadUpdate() {
  if (!autoUpdaterRef) {
    throw new Error("downloadUpdate called before auto-updater was initialised");
  }
  if (state.status !== STATES.AVAILABLE) {
    throw new Error(
      `No update is waiting to download (status: ${state.status}) — check for updates first.`
    );
  }
  log("info", `User asked to install ${state.version}`);
  setState({ status: STATES.DOWNLOADING, progress: 0, error: null });
  try {
    await autoUpdaterRef.downloadUpdate();
  } catch (err) {
    const message = (err && err.message) || String(err);
    setState({ status: STATES.ERROR, error: message });
    throw new Error(`Downloading GAIA ${state.version || ""} failed: ${message}`);
  }
}

/** Restart into a downloaded update, asking first if work would be cut off. */
async function restartToInstall() {
  if (!autoUpdaterRef || state.status !== STATES.DOWNLOADED) {
    throw new Error(`No downloaded update to install (status: ${state.status}).`);
  }
  if (!(await confirmRestartRef())) {
    log("info", "Restart put off: work is still running — will install on next quit");
    return;
  }
  log("info", "User chose to restart from the update chip — calling quitAndInstall");
  autoUpdaterRef.quitAndInstall(false, true);
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
  // Re-resolve so a feed written to config after init() is picked up here.
  if (!forwardFeedUrl && !state.pinnedVersion) {
    if (!_applyForwardFeed()) return;
  }
  if (checkInProgress) {
    log("info", "Skipping check — another check is already in progress");
    return;
  }
  checkInProgress = true;
  setState({ status: STATES.CHECKING, error: null });
  try {
    // No configured feed: aim at the newest GitHub release on this channel,
    // re-read every check so a new release is found.
    if (!forwardFeedUrl && !state.pinnedVersion && !(await _applyGithubFeed())) {
      return;
    }
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
        // quitAndInstall starts the installer at once, so ask about running
        // work here; the quit that follows doesn't ask again.
        if (!(await confirmRestartRef())) {
          log("info", "Restart put off: work is still running — will install on next quit");
          return;
        }
        log("info", "User chose to restart — calling quitAndInstall");
        autoUpdaterRef.quitAndInstall(false, true);
      } else {
        log("info", "User deferred restart — will install on next quit");
      }
    } catch (err) {
      const message = (err && err.message) || String(err);
      log("error", "Failed to show restart dialog:", message);
      electronApi.dialog.showErrorBox(
        "GAIA update",
        `GAIA couldn't finish asking about the update restart: ${message}\n\n` +
          "The update installs the next time you quit GAIA."
      );
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
  ipcMain.handle("gaia:update:set-auto-update", async (_event, enabled) => {
    await setAutoUpdate(enabled);
    return getState();
  });
  ipcMain.handle("gaia:update:set-include-prereleases", async (_event, enabled) => {
    await setIncludePrereleases(enabled);
    return getState();
  });
  ipcMain.handle("gaia:update:download", async () => {
    await downloadUpdate();
    return getState();
  });
  ipcMain.handle("gaia:update:restart", async () => {
    await restartToInstall();
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
    "gaia:update:set-auto-update",
    "gaia:update:set-include-prereleases",
    "gaia:update:download",
    "gaia:update:restart",
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
 * @param {object} options
 * @param {() => Promise<boolean>} options.confirmRestart - Whether "Restart now"
 *   may go ahead; asked before the installer starts. Required: without it an
 *   update restart would silently cut off a running answer.
 */
function init(mainWindow, options = {}) {
  if (typeof options.confirmRestart !== "function") {
    throw new TypeError(
      "auto-updater init needs options.confirmRestart, so Restart now can ask before it stops running work"
    );
  }
  mainWindowRef = mainWindow || null;
  confirmRestartRef = options.confirmRestart;

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
  const loaded = _loadAutoUpdate();
  state.autoUpdate = loaded.autoUpdate;
  state.includePrereleases = loaded.includePrereleases;
  state.configError = loaded.error;

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
    // Off by default; a pin always suppresses it so the scheduled forward-only
    // check cannot silently download/install a newer version (AC2).
    autoUpdaterRef.autoDownload = !savedPin && state.autoUpdate;
    autoUpdaterRef.autoInstallOnAppQuit = true;
    autoUpdaterRef.disableWebInstaller = true;
    autoUpdaterRef.allowDowngrade = false;
    autoUpdaterRef.allowPrerelease = _prereleasesAllowed();

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

  // Point the updater at the configured R2 generic feed (#1724), or at GitHub
  // releases when none is configured. When a pin is active, installVersion()
  // owns the feed (rollback path), so skip.
  let feedReady = true;
  if (!savedPin) {
    feedReady = _applyForwardFeed();
  }

  initialized = true;

  if (!feedReady) {
    log("error", "Auto-updater initialized but its feed could not be set — see the error above");
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
  setAutoUpdate,
  setIncludePrereleases,
  downloadUpdate,
  restartToInstall,
  STATES,
};
