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
 *   - On `update-downloaded`, the chip offers the restart; nothing interrupts
 *   - Renderer integration via IPC channel `gaia:update:status`
 *   - Disabled entirely via GAIA_DISABLE_UPDATE=1 env var (CI / dev / corp)
 *   - macOS never downloads or installs: ad-hoc signed builds can't replace
 *     themselves (#7061), so a found update links to its release page
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
 *   - revertUpdate()          → go back to the version before the last update
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
const crypto = require("crypto");
const zlib = require("zlib");

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

// The picker shows only the N most-recent installable releases (rolling back
// more than a few versions is rare). Older versions stay reachable via the
// "browse all on GitHub" link in the picker, so this display cap never makes a
// release unreachable.
const MAX_RELEASES_SHOWN = 10;

const RELEASES_PAGE = "https://github.com/amd/gaia/releases";
const RELEASE_DOWNLOADS = `${RELEASES_PAGE}/download`;

// The atom feed and download URLs don't count against the REST API's 60
// anonymous calls an hour, which one busy network can use up for everyone.
const RELEASES_ATOM = `${RELEASES_PAGE}.atom`;
const PICKER_FEED_PAGES = 3;

// Where a restart notes the update it started: a silent install that fails
// shows nothing, so the next launch is what finds out.
const INSTALL_ATTEMPT_PATH = path.join(os.homedir(), ".gaia", "update-attempt.json");
const PREVIOUS_VERSION_KEY = "previousAppVersion";
const LAUNCHES_SINCE_UPDATE_KEY = "launchesSinceUpdate";
// Same cap as the TUI's sumsAtOnce, to go easy on proxies.
const RELEASE_CHECKS_AT_ONCE = 8;
// A backend that dies weeks after an update isn't the update's fault.
const START_FAILURE_REVERT_LAUNCHES = 3;

// The installer removes the old version before unpacking the new one.
const MIN_INSTALL_FREE_BYTES = 1024 * 1024 * 1024;

// A black-holed connection must fail, not leave "Checking…" up forever.
const GITHUB_TIMEOUT_MS = 20 * 1000;

/** The generic feed for one release's assets. */
function _releaseFeed(tag, extra = {}) {
  return {
    provider: "generic",
    url: `${RELEASE_DOWNLOADS}/${tag}/`,
    // GitHub answers multi-range requests 501, which loses the delta download.
    useMultipleRangeRequest: false,
    ...extra,
  };
}

/** fetch() with a timeout; rejects with `.kind` "timeout" or "network". */
async function _githubFetch(url, options = {}) {
  try {
    return await fetch(url, {
      headers: { "User-Agent": "GAIA-Agent-UI" },
      signal: AbortSignal.timeout(GITHUB_TIMEOUT_MS),
      ...options,
    });
  } catch (err) {
    const timedOut = err && (err.name === "TimeoutError" || err.name === "AbortError");
    throw Object.assign(new Error((err && err.message) || String(err)), {
      kind: timedOut ? "timeout" : "network",
    });
  }
}

/**
 * A delta update diffs the installer electron-updater cached at install time
 * against the new one, using the running version's blockmap. It guesses that
 * blockmap's URL by swapping version strings inside the new one, which misses
 * every release candidate's tag ("v0.25.4-rc3" holds no "0.25.4-rc.3"), and a
 * fresh install has no blockmap cached. Without one the update downloads in
 * full, so fetch it from the running version's own release ahead of time.
 */
async function _seedInstalledBlockmap() {
  if (process.platform !== "win32" || typeof autoUpdaterRef.getOrCreateDownloadHelper !== "function") {
    return;
  }
  const version = state.currentVersion;
  const { cacheDir } = await autoUpdaterRef.getOrCreateDownloadHelper();
  const installer = path.join(cacheDir, "installer.exe");
  const blockmap = path.join(cacheDir, "current.blockmap");
  const seededFor = path.join(cacheDir, "gaia-blockmap-version");
  if (!version || !fs.existsSync(installer)) return;
  const seeded = _readText(seededFor);
  if (seeded === `${version}:not-this-release`) return;
  if (fs.existsSync(blockmap) && seeded === version) return;

  const base = `${RELEASE_DOWNLOADS}/${_tagForVersion(version)}`;
  const manifest = await _githubFetch(`${base}/${_platformManifest()}`);
  if (!manifest.ok) throw _httpError(manifest.status);
  const yml = await manifest.text();
  const file = /^path:\s*(\S+)/m.exec(yml);
  const sha512 = /^sha512:\s*(\S+)/m.exec(yml);
  if (!file || !sha512) throw new Error(`${_platformManifest()} for ${version} names no installer`);
  // The cached installer must be this release's, or the diff comes out wrong.
  if ((await _sha512File(installer)) !== sha512[1]) {
    log("info", `The cached installer isn't GAIA ${version}'s release file: the next update downloads in full`);
    fs.writeFileSync(seededFor, `${version}:not-this-release`);
    return;
  }
  const resp = await _githubFetch(`${base}/${file[1]}.blockmap`);
  if (!resp.ok) throw _httpError(resp.status);
  const data = Buffer.from(await resp.arrayBuffer());
  JSON.parse(zlib.gunzipSync(data).toString());
  fs.writeFileSync(blockmap, data);
  fs.writeFileSync(seededFor, version);
  log("info", `Cached GAIA ${version}'s blockmap, so the next update downloads only what changed`);
}

function _readText(file) {
  try {
    return fs.readFileSync(file, "utf8").trim();
  } catch {
    return null;
  }
}

function _sha512File(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha512");
    fs.createReadStream(file)
      .on("error", reject)
      .on("data", (chunk) => hash.update(chunk))
      .on("end", () => resolve(hash.digest("base64")));
  });
}

function _httpError(status) {
  return Object.assign(new Error(`HTTP ${status}`), { kind: "http", status });
}

/** One page of releases.atom as [{tag_name, published_at, html_url, prerelease}]. */
async function _fetchFeedPage(after) {
  const url = after ? `${RELEASES_ATOM}?after=${encodeURIComponent(after)}` : RELEASES_ATOM;
  const resp = await _githubFetch(url);
  if (!resp.ok) throw _httpError(resp.status);
  const xml = await resp.text();
  if (!xml.includes("<feed")) {
    throw Object.assign(new Error("not an atom feed"), { kind: "garbled" });
  }
  const releases = [];
  for (const [, entry] of xml.matchAll(/<entry>([\s\S]*?)<\/entry>/g)) {
    const id = /<id>[^<]*\/([^/<]+)<\/id>/.exec(entry);
    if (!id) continue;
    const updated = /<updated>([^<]+)<\/updated>/.exec(entry);
    releases.push({
      tag_name: id[1],
      published_at: updated ? updated[1] : null,
      html_url: `${RELEASES_PAGE}/tag/${id[1]}`,
      prerelease: id[1].includes("-"),
    });
  }
  return releases;
}

/**
 * The newest releases from the atom feed, plus the newest final release, which
 * a run of candidates can push off the feed's first page. Rejects with `.kind`
 * set to "timeout", "network", "http" (with `.status`) or "garbled", so each
 * caller can word its own message.
 */
async function _fetchReleases(pages = 1) {
  const releases = [];
  let after = null;
  for (let page = 0; page < pages; page++) {
    const batch = await _fetchFeedPage(after);
    releases.push(...batch);
    if (batch.length === 0) break;
    after = batch[batch.length - 1].tag_name;
  }
  const latest = await _githubFetch(`${RELEASES_PAGE}/latest/download/${_platformManifest()}`);
  if (latest.ok) {
    const version = /^version:\s*(\S+)/m.exec(await latest.text());
    const tag = version && `v${version[1].replace(/^v/, "")}`;
    if (tag && !releases.some((r) => r.tag_name === tag)) {
      releases.push({
        tag_name: tag,
        published_at: null,
        html_url: `${RELEASES_PAGE}/tag/${tag}`,
        prerelease: false,
      });
    }
  } else if (latest.status !== 404) {
    throw _httpError(latest.status);
  }
  return releases;
}

/**
 * Whether `tag` carries this platform's manifest and the installer it names.
 * A release is listed before its assets finish uploading, and macOS manifests
 * can name a zip that was never attached, so the file itself is checked.
 */
async function _releaseHasUpdate(tag) {
  const base = `${RELEASE_DOWNLOADS}/${tag}`;
  const manifest = await _githubFetch(`${base}/${_platformManifest()}`);
  if (manifest.status === 404) return false;
  if (!manifest.ok) throw _httpError(manifest.status);
  const path = /^path:\s*(\S+)/m.exec(await manifest.text());
  if (!path) return false;
  const installer = await _githubFetch(`${base}/${path[1]}`, { method: "HEAD" });
  if (installer.status === 404) return false;
  if (!installer.ok) throw _httpError(installer.status);
  return true;
}

/** The user-facing sentence for a _fetchReleases failure while `doing`. */
function _releasesErrorMessage(err, doing) {
  switch (err && err.kind) {
    case "timeout":
      return `GitHub didn't answer within ${GITHUB_TIMEOUT_MS / 1000} seconds when GAIA tried to ${doing} — try again later, or download an installer from ${RELEASES_PAGE}.`;
    case "http": {
      const limited = err.status === 403 || err.status === 429;
      return (
        `GitHub answered HTTP ${err.status} when GAIA tried to ${doing}` +
        (limited ? " — its hourly limit for this network may be used up; try again later" : " — try again later") +
        `, or download an installer from ${RELEASES_PAGE}.`
      );
    }
    case "garbled":
      return `GitHub sent a response GAIA couldn't read when it tried to ${doing} (${err.message}) — try again later, or download an installer from ${RELEASES_PAGE}.`;
    default:
      return `Couldn't reach GitHub to ${doing} (${(err && err.message) || err}) — check your connection, or download an installer from ${RELEASES_PAGE}.`;
  }
}

/**
 * Whether this build can replace itself. macOS builds are ad-hoc signed, and
 * Squirrel.Mac rejects any other ad-hoc build as the update (#7061), so a Mac
 * update is offered as a download from its release page instead.
 */
function _selfInstalls() {
  return process.platform !== "darwin";
}

/** The release page for `version`, where its installers are downloaded by hand. */
function _releasePage(version) {
  return `${RELEASES_PAGE}/tag/${_tagForVersion(version)}`;
}

/** The refusal for an install this build can't do itself. */
function _manualInstallError(version) {
  const url = version ? _releasePage(version) : RELEASES_PAGE;
  return _userError(
    "manualInstall",
    { url },
    `GAIA can't update itself on this Mac yet. Download the new version from ${url} and install it over this one.`
  );
}

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
  previousVersion: null,
  startFailed: false,
  autoUpdate: false,
  // Offer release candidates too; off by default. A release-candidate build
  // is offered newer candidates without it.
  includePrereleases: false,
  // update-config.json could not be read; kept apart from `error` so a later
  // check's status never hides it.
  configError: null,
  // The last restart didn't install; stays until an install works.
  installFailed: null,
  // Why the last unasked check or download failed; Settings shows it quietly.
  lastCheckError: null,
  // The update has downloaded and its backend is being fetched before the restart.
  preparing: false,
  // This build can't install updates itself (macOS, #7061): an update is offered
  // as a download from `downloadUrl` instead.
  manualInstall: !_selfInstalls(),
  // The release page of the offered update, or null.
  downloadUrl: null,
};

let mainWindowRef = null;
/** Asks before an update restart would cut off running work (set by init). */
let confirmRestartRef = null;
// Put the update's backend in the local cache before the restart (set by init).
let prefetchBackendRef = null;
let isBackendPrefetchedRef = null;
let checkInProgress = false;
// True while a check nobody asked for runs, so its failure stays out of the UI.
let backgroundCheck = false;
// The last check was asked for, so the download it starts may report failure.
let askedCheck = false;
// A routine check is running with this version already downloaded.
let heldDownloadVersion = null;
// The downloaded update was given up on, so quitting must not install it.
let abandonedDownload = false;
// The silent install failed, so the next try shows the installer's window.
let wizardNextInstall = false;
// Picking a version asks for the restart too, so no second click is needed.
let restartWhenDownloaded = false;
// Settles once the restart a picked version asked for has run or been refused.
let pendingRestart = null;
let restartRefusal = null;
// Settles once the last update-downloaded is handled, prefetch and restart
// included. electron-updater doesn't wait for its listeners, so callers do.
let downloadHandled = null;
// Bumped when a download is given up on, so a prefetch that outlives it can tell.
let downloadEpoch = 0;
// A prefetch per version, so a repeat download waits on it instead of racing it.
const prefetching = new Map();
// The user asked for this download, so it may install on quit with auto-update off.
let installRequested = false;
// The user chose to restart into the update: the quit runs its installer.
let installingNow = false;
// The file was unreadable at launch, so a pin it holds was never loaded.
let pinUnknown = false;
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
    log("warn", "Failed to read version pin — update checks pause until the file is fixed:", err && err.message);
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

/**
 * Re-read the shared settings before acting on them: the Terminal UI can turn
 * auto-update off while this app runs, and that must stop the next download.
 */
function _refreshPreferences() {
  const loaded = _loadAutoUpdate();
  setState({
    autoUpdate: loaded.autoUpdate,
    includePrereleases: loaded.includePrereleases,
    configError: loaded.error,
  });
  if (pinUnknown && !loaded.error) {
    pinUnknown = false;
    const pin = _loadPin();
    if (pin) {
      log("info", `Settings file readable again — honouring its pin to ${pin}`);
      setState({ pinnedVersion: pin });
      if (autoUpdaterRef) {
        autoUpdaterRef.setFeedURL(_releaseFeed(pin));
      }
    }
  }
  _applyInstallPolicy();
}

function _applyInstallPolicy() {
  if (!autoUpdaterRef) return;
  const selfInstalls = _selfInstalls();
  autoUpdaterRef.autoDownload = selfInstalls && state.autoUpdate && !state.pinnedVersion;
  autoUpdaterRef.autoInstallOnAppQuit =
    selfInstalls &&
    (state.autoUpdate || installRequested) &&
    !abandonedDownload &&
    !wizardNextInstall &&
    !state.preparing;
  autoUpdaterRef.allowPrerelease = _prereleasesAllowed();
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
 * An error the UI shows in the user's language: `code` names the message
 * update.err.<code> and `params` fills it. The message is the English, for logs.
 */
function _userError(code, params, message) {
  return Object.assign(new Error(message), { code, params: params || {} });
}

// Marks an error message as a coded one, for the renderer to translate.
const USER_ERROR_MARK = "gaia-update-error:";

// Codes the UI has an update.err.<code> message for; any other code is a cause.
const USER_ERROR_CODES = new Set([
  "checkBusy",
  "configBusy",
  "diskSpace",
  "downloadFailed",
  "failedWith",
  "manualInstall",
  "noEarlier",
  "noInstaller",
  "offline",
  "pinFailed",
  "saveFailed",
]);

function _isUserCode(code) {
  return USER_ERROR_CODES.has(code);
}

/** IPC keeps only an error's message, so the code travels inside it. */
function _forRenderer(err) {
  if (!err || !_isUserCode(err.code)) return err;
  const { code, params, note } = err;
  return new Error(`${USER_ERROR_MARK}${JSON.stringify({ code, params, note, message: err.message })}`);
}

/**
 * Merge `patch` into update-config.json. Propagates write errors so a failed
 * pin write can abort a rollback (AC2 must not be silently broken). Callers
 * that can tolerate a failed write (e.g. clearPin) should catch.
 *
 * A file that can't be read or parsed is refused, never overwritten: it holds
 * keys this app doesn't own (feedUrl, the Terminal UI's pin). The write goes
 * to a temp file and is renamed over, so a crash never leaves half a file.
 */
function _writeConfig(patch) {
  fs.mkdirSync(path.dirname(UPDATE_CONFIG_PATH), { recursive: true });
  _withConfigLock(() => _mergeConfig(patch));
}

function _mergeConfig(patch) {
  let existing = {};
  if (fs.existsSync(UPDATE_CONFIG_PATH)) {
    try {
      existing = JSON.parse(fs.readFileSync(UPDATE_CONFIG_PATH, "utf8"));
    } catch (err) {
      throw new Error(
        `${UPDATE_CONFIG_PATH} cannot be read (${(err && err.message) || err}), so the setting was not saved — ` +
          "fix the file by hand or delete it."
      );
    }
    if (!existing || typeof existing !== "object" || Array.isArray(existing)) {
      throw new Error(
        `${UPDATE_CONFIG_PATH} does not hold a settings object, so the setting was not saved — ` +
          "fix the file by hand or delete it."
      );
    }
  }
  const tmp = `${UPDATE_CONFIG_PATH}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify({ ...existing, ...patch }, null, 2), "utf8");
    fs.renameSync(tmp, UPDATE_CONFIG_PATH);
  } finally {
    fs.rmSync(tmp, { force: true });
  }
}

// The Terminal UI (tui/internal/update/config.go) honours the same lock file,
// so neither app's read-modify-write drops the other's change.
// Short: the wait blocks the main process. The Terminal UI holds it for milliseconds.
const LOCK_WAIT_MS = 500;
const LOCK_STALE_MS = 10000;

function _withConfigLock(fn) {
  const lock = `${UPDATE_CONFIG_PATH}.lock`;
  const deadline = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    let openErr;
    try {
      fs.closeSync(fs.openSync(lock, "wx"));
      break;
    } catch (err) {
      // Windows reports a lock still being deleted as EPERM/EACCES.
      if (!err || !["EEXIST", "EPERM", "EACCES"].includes(err.code)) throw err;
      openErr = err;
    }
    let stat = null;
    try {
      stat = fs.statSync(lock);
    } catch (err) {
      if (err.code !== "ENOENT") throw err;
    }
    if (!stat && openErr.code !== "EEXIST" && process.platform !== "win32") {
      throw new Error(
        `Cannot save update settings: ${path.dirname(UPDATE_CONFIG_PATH)} does not allow new files ` +
          `(${openErr.message}) — fix its permissions.`
      );
    }
    let staleErr = null;
    if (stat && Date.now() - stat.mtimeMs > LOCK_STALE_MS) {
      // Left by a crashed app. Two takers racing on the same stale lock can
      // both get in; rare enough to accept for a settings file.
      const aside = `${lock}.${process.pid}.stale`;
      try {
        fs.renameSync(lock, aside);
        fs.rmSync(aside, { force: true });
        continue;
      } catch (err) {
        if (err.code === "ENOENT") continue;
        if (!["EPERM", "EACCES"].includes(err.code)) throw err;
        staleErr = err;
      }
    }
    if (Date.now() > deadline) {
      if (staleErr) {
        throw new Error(
          `Cannot remove ${lock}, left by a GAIA app that stopped (${staleErr.message}) — fix the ` +
            `permissions of ${path.dirname(UPDATE_CONFIG_PATH)} or delete the lock file by hand.`
        );
      }
      if (!stat) {
        throw new Error(
          `Cannot lock ${UPDATE_CONFIG_PATH} (${openErr.message}) — check the permissions of ` +
            `${path.dirname(UPDATE_CONFIG_PATH)}.`
        );
      }
      throw _userError(
        "configBusy",
        {},
        `${UPDATE_CONFIG_PATH} is busy (another GAIA app holds ${lock}) — try again in a moment.`
      );
    }
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 25);
  }
  try {
    return fn();
  } finally {
    fs.rmSync(lock, { force: true });
  }
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
  return process.arch === "arm64" ? "latest-linux-arm64.yml" : "latest-linux.yml";
}

/** GAIA releases on this install's channel, newest first. */
function _channelReleases(releases) {
  const allowPrerelease = _prereleasesAllowed();
  return releases
    .filter(
      (r) =>
        TAG_RE.test(r.tag_name || "") &&
        !Number.isNaN(_compareVersions(r.tag_name, "0.0.0")) &&
        (allowPrerelease || !r.prerelease)
    )
    .sort((a, b) => _compareVersions(b.tag_name, a.tag_name));
}

/**
 * Point the feed at the newest GitHub release on this install's channel that
 * carries this platform's installer and update manifest; returns its tag.
 * Throws an actionable error when GitHub can't be read. Returns null, in the
 * NO_CHANNEL state, when no release has an update for this platform.
 */
async function _applyGithubFeed() {
  const manifest = _platformManifest();
  let tag = null;
  try {
    for (const release of _channelReleases(await _fetchReleases())) {
      if (await _releaseHasUpdate(release.tag_name)) {
        tag = release.tag_name;
        break;
      }
    }
  } catch (err) {
    throw new Error(_releasesErrorMessage(err, "check for updates"));
  }
  if (!tag) {
    const error =
      `No GAIA release publishes a desktop update for this platform yet (it needs ${manifest} ` +
      `and its installer), so GAIA can't update itself here — download an installer from ${RELEASES_PAGE}.`;
    setState({ status: STATES.NO_CHANNEL, error });
    log("warn", error);
    return null;
  }
  autoUpdaterRef.setFeedURL(_releaseFeed(tag, { channel: DEFAULT_CHANNEL }));
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

  let installable;
  try {
    // Twice the display cap, so releases with no update here still fill the list.
    const candidates = _channelReleases(await _fetchReleases(PICKER_FEED_PAGES)).slice(
      0,
      MAX_RELEASES_SHOWN * 2
    );
    const ready = await _mapLimited(candidates, RELEASE_CHECKS_AT_ONCE, (r) =>
      _releaseHasUpdate(r.tag_name)
    );
    installable = candidates.filter((_, i) => ready[i]).slice(0, MAX_RELEASES_SHOWN);
  } catch (err) {
    return { error: _releasesErrorMessage(err, "list releases") };
  }

  return installable.map((r) => {
    const tag = r.tag_name;
    // Version is tag without leading 'v'
    const version = tag.startsWith("v") ? tag.slice(1) : tag;
    return {
      version,
      tag,
      date: r.published_at,
      notesUrl: r.html_url,
      isCurrent: currentVersion ? _compareVersions(version, currentVersion) === 0 : false,
      isPinned: pinnedVersion ? tag === pinnedVersion : false,
    };
  });
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
 * user puts off the restart, the pin is already in place for AC2.
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
  if (!_selfInstalls()) {
    throw _manualInstallError(tag.replace(/^v/, ""));
  }
  if (checkInProgress) {
    throw _userError(
      "checkBusy",
      {},
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
    if (err && err.code === "configBusy") throw err;
    throw _userError(
      "pinFailed",
      { path: UPDATE_CONFIG_PATH, reason: msg },
      `Failed to persist version pin — rollback aborted: ${msg}`
    );
  }
  setState({ pinnedVersion: tag, previousVersion: null, startFailed: false });
  // A pin always stops background downloads; this one is downloaded below.
  installRequested = true;
  restartWhenDownloaded = true;
  _applyInstallPolicy();

  checkInProgress = true;
  try {
    autoUpdaterRef.allowDowngrade = true;
    autoUpdaterRef.setFeedURL(_releaseFeed(tag));

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
    // restarts into it.
    await autoUpdaterRef.checkForUpdates();
    if (state.status !== STATES.AVAILABLE) {
      throw _userError(
        "noInstaller",
        { tag, url: RELEASES_PAGE },
        `GAIA ${tag} has no installer for this platform at its release feed` +
          (state.error ? `: ${state.error}` : ".") +
          ` Download it from ${RELEASES_PAGE} instead.`
      );
    }
    setState({ status: STATES.DOWNLOADING, progress: 0, error: null });
    downloadHandled = null;
    await autoUpdaterRef.downloadUpdate();
  } catch (err) {
    restartWhenDownloaded = false;
    // Don't leave the app pinned to a version that never installed.
    const pinNote = _restorePinAfterFailedInstall(previousPin, tag);
    const msg = (err && err.message) || String(err);
    setState({ status: STATES.ERROR, error: msg });
    // A coded cause keeps its code; any other shows its own text.
    const coded = err && _isUserCode(err.code);
    throw Object.assign(new Error(`${msg} ${pinNote.message}`), {
      code: coded ? err.code : "failedWith",
      params: coded ? err.params : { reason: msg },
      note: { code: pinNote.code, params: pinNote.params },
    });
  } finally {
    checkInProgress = false;
  }
  await downloadHandled;
  if (restartRefusal) {
    const refusal = restartRefusal;
    restartRefusal = null;
    throw refusal;
  }
}

/** Undo the pin a failed install wrote; returns the sentence saying where things stand. */
function _restorePinAfterFailedInstall(previousPin, failedTag) {
  try {
    _savePin(previousPin || null);
  } catch (err) {
    // The file still pins failedTag; say so rather than claim otherwise.
    log("error", "Failed to restore the pin after a failed install:", err && err.message);
    const reason = (err && err.message) || String(err);
    return {
      code: "pinStuck",
      params: { tag: failedTag, path: UPDATE_CONFIG_PATH, reason },
      message:
        `The pin to ${failedTag} could not be undone in ${UPDATE_CONFIG_PATH} ` +
        `(${reason}) — fix that file, then use Unpin in Settings → General → Updates.`,
    };
  }
  if (!previousPin) {
    clearPin();
    return { code: "notPaused", params: {}, message: "Updates are not paused." };
  }
  setState({ pinnedVersion: previousPin });
  autoUpdaterRef.setFeedURL(_releaseFeed(previousPin));
  return { code: "stillPinned", params: { tag: previousPin }, message: `Still pinned to ${previousPin}.` };
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
  setState({ pinnedVersion: null, previousVersion: previousVersion() });
  installRequested = false;
  if (state.status === STATES.DOWNLOADED) _abandonDownload();

  if (autoUpdaterRef) {
    _applyInstallPolicy();
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
    if (err && err.code === "configBusy") throw err;
    const reason = (err && err.message) || String(err);
    throw _userError(
      "saveFailed",
      { path: UPDATE_CONFIG_PATH, reason },
      `Couldn't save the auto-update setting to ${UPDATE_CONFIG_PATH}: ${reason}`
    );
  }
  setState({ autoUpdate: enabled });
  _applyInstallPolicy();
  log("info", `Auto-update ${enabled ? "enabled" : "disabled"}`);
  // Turning it on with an update already offered means "install it".
  if (enabled && _selfInstalls() && !state.pinnedVersion && state.status === STATES.AVAILABLE) {
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
    if (err && err.code === "configBusy") throw err;
    const reason = (err && err.message) || String(err);
    throw _userError(
      "saveFailed",
      { path: UPDATE_CONFIG_PATH, reason },
      `Couldn't save the release-candidate setting to ${UPDATE_CONFIG_PATH}: ${reason}`
    );
  }
  setState({ includePrereleases: enabled });
  if (autoUpdaterRef) {
    autoUpdaterRef.allowPrerelease = _prereleasesAllowed();
  }
  const offered = state.status === STATES.DOWNLOADED || state.status === STATES.AVAILABLE;
  if (offered && _isPrereleaseVersion(state.version) && !_prereleasesAllowed()) {
    _abandonDownload();
  }
  log("info", `Release-candidate updates ${enabled ? "enabled" : "disabled"}`);
}

/** Stop offering the current update, and don't let a quit install it. */
function _abandonDownload() {
  log("info", `No longer offering GAIA ${state.version}`);
  abandonedDownload = true;
  downloadEpoch += 1;
  installRequested = false;
  restartWhenDownloaded = false;
  setState({ status: STATES.IDLE, version: null, progress: 0, releaseNotes: null });
  _applyInstallPolicy();
}

/** Promise.all over items, with at most `limit` calls in flight. */
async function _mapLimited(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

function _readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8")) || {};
  } catch (err) {
    if (err && err.code !== "ENOENT") log("warn", `Couldn't read ${file}:`, err.message);
    return {};
  }
}

function _runningVersion() {
  if (state.currentVersion) return state.currentVersion;
  // eslint-disable-next-line global-require
  return require("electron").app.getVersion();
}

/**
 * The older version a bad update can go back to, or null. Works before init():
 * a backend that won't set up stops the launch before the updater starts.
 * A pin means the version was chosen, so there's nothing to undo.
 */
function previousVersion() {
  if (isDisabled()) return null;
  const running = _runningVersion();
  const config = _readJsonFile(UPDATE_CONFIG_PATH);
  if (config.pinnedVersion) return null;
  const attempt = _readJsonFile(INSTALL_ATTEMPT_PATH);
  const earlier = attempt.from && attempt.from !== running ? attempt.from : config[PREVIOUS_VERSION_KEY];
  return earlier && _compareVersions(earlier, running) < 0 ? earlier : null;
}

/** "0.25.4-rc.2" → "v0.25.4-rc2": candidate builds carry a dot their tags don't. */
function _tagForVersion(version) {
  return `v${version.replace(/-rc\.(\d+)$/, "-rc$1")}`;
}

/**
 * Go back to the version before the last update. Pins it, so the update that
 * went wrong isn't reinstalled, and resolves once the restart has run or been
 * refused (state then says why).
 */
async function revertUpdate() {
  const version = previousVersion();
  if (!version) {
    throw _userError(
      "noEarlier",
      { url: RELEASES_PAGE },
      `There is no earlier GAIA version to go back to on this PC. Pick one in Settings → General → Updates, or download it from ${RELEASES_PAGE}.`
    );
  }
  log("info", `Going back to GAIA ${version}`);
  const tag = _tagForVersion(version);
  const { startFailed } = state;
  try {
    await installVersion(tag);
  } catch (err) {
    // A refused restart leaves the pin and the download: neither may outlive it.
    if (state.pinnedVersion === tag) clearPin();
    setState({ startFailed });
    throw err;
  }
}

/** True for the first few launches after an update landed, the only time a failed start blames it. */
function _updateIsRecent() {
  // An attempt from the running version is a failed install, not an update.
  const { from } = _readJsonFile(INSTALL_ATTEMPT_PATH);
  if (from && from !== _runningVersion()) return true;
  const launches = _readJsonFile(UPDATE_CONFIG_PATH)[LAUNCHES_SINCE_UPDATE_KEY];
  return Number.isInteger(launches) && launches <= START_FAILURE_REVERT_LAUNCHES;
}

/** The app opened soon after an update but its backend didn't come up: offer the way back. */
function reportStartFailure() {
  const previous = previousVersion();
  if (previous && _updateIsRecent()) setState({ startFailed: true, previousVersion: previous });
}

/** Count this launch toward the window in which a failed start offers to go back. */
function _countLaunchSinceUpdate() {
  const config = _readJsonFile(UPDATE_CONFIG_PATH);
  const launches = config[LAUNCHES_SINCE_UPDATE_KEY];
  if (!config[PREVIOUS_VERSION_KEY] || !Number.isInteger(launches)) return;
  if (launches > START_FAILURE_REVERT_LAUNCHES) return;
  try {
    _writeConfig({ [LAUNCHES_SINCE_UPDATE_KEY]: launches + 1 });
  } catch (err) {
    log("warn", "Couldn't count this launch since the update:", err && err.message);
  }
}

/** A slow backend came up after all, so there's nothing to offer a way back from. */
function clearStartFailure() {
  if (state.startFailed) setState({ startFailed: false });
}

/** Note the install a quit is about to start, for the next launch to judge. */
function _recordInstallAttempt() {
  try {
    fs.mkdirSync(path.dirname(INSTALL_ATTEMPT_PATH), { recursive: true });
    fs.writeFileSync(
      INSTALL_ATTEMPT_PATH,
      JSON.stringify({ from: state.currentVersion, target: state.version })
    );
  } catch (err) {
    log("warn", "Couldn't note the update attempt:", err && err.message);
  }
}

/** Running the version an install was meant to replace means it failed. */
function _readInstallOutcome() {
  let attempt;
  try {
    attempt = JSON.parse(fs.readFileSync(INSTALL_ATTEMPT_PATH, "utf8"));
  } catch (err) {
    if (err && err.code !== "ENOENT") {
      log("warn", "Couldn't read the last update attempt:", err.message);
      return;
    }
    _countLaunchSinceUpdate();
    return;
  }
  if (attempt.from !== state.currentVersion) {
    try {
      _writeConfig({ [PREVIOUS_VERSION_KEY]: attempt.from, [LAUNCHES_SINCE_UPDATE_KEY]: 1 });
    } catch (err) {
      // The attempt file stays, so previousVersion() still knows the way back.
      log("warn", "Couldn't save the previous version:", err && err.message);
      return;
    }
    fs.rmSync(INSTALL_ATTEMPT_PATH, { force: true });
    return;
  }
  wizardNextInstall = true;
  state.installFailed = { target: attempt.target, from: attempt.from };
  // Never stay pinned to a version that didn't land.
  if (state.pinnedVersion === _tagForVersion(attempt.target)) {
    try {
      _savePin(null);
      state.pinnedVersion = null;
    } catch (err) {
      log("warn", "Couldn't clear the pin to the failed install:", err && err.message);
    }
  }
  log("error", `GAIA ${attempt.target} didn't install; still on ${attempt.from}`);
  // Report the failure once: a stale attempt would keep install-on-quit off for good.
  try {
    fs.rmSync(INSTALL_ATTEMPT_PATH, { force: true });
  } catch (err) {
    log("warn", "Couldn't clear the failed update attempt:", err && err.message);
  }
}

/** The quit path's check: a quit can't wait for the async one. */
function _diskTooFullToInstall() {
  try {
    const stats = fs.statfsSync(os.homedir());
    return stats.bavail * stats.bsize < MIN_INSTALL_FREE_BYTES;
  } catch (err) {
    log("warn", "Couldn't read free disk space:", err && err.message);
    return false;
  }
}

/** Refuse a restart the installer can't finish, while the app still works. */
async function _requireInstallSpace() {
  let free;
  try {
    const stats = await fs.promises.statfs(os.homedir());
    free = stats.bavail * stats.bsize;
  } catch (err) {
    log("warn", "Couldn't read free disk space:", err && err.message);
    return;
  }
  if (free < MIN_INSTALL_FREE_BYTES) {
    const mb = Math.round(free / (1024 * 1024));
    throw _userError(
      "diskSpace",
      { need: "1 GB", free: `${mb} MB` },
      `GAIA needs about 1 GB free to install this update, and this disk has ` +
        `${mb} MB. Free some space, then restart from the update chip.`
    );
  }
}

/**
 * Setup on the next launch needs PyPI unless the backend was prefetched, so
 * restarting offline would leave no working app until the connection returns.
 * Only a PC with no network at all counts: probing a host would block people
 * behind proxies.
 */
function _isOffline() {
  return Boolean(electronApi && electronApi.net && !electronApi.net.isOnline());
}

function _backendCached() {
  return Boolean(isBackendPrefetchedRef && state.version && isBackendPrefetchedRef(state.version));
}

function _requireNetwork() {
  if (_isOffline() && !_backendCached()) {
    throw _userError(
      "offline",
      {},
      "GAIA needs the internet to finish this update. Connect, then restart from the update chip."
    );
  }
}

/**
 * Fetch the update's backend while the app still runs. A failure only means the
 * first launch downloads it instead, as without a prefetch, so it is logged and
 * the restart keeps its internet check.
 */
async function _prefetchBackend(version) {
  if (!prefetchBackendRef || !version) return;
  if (isBackendPrefetchedRef && isBackendPrefetchedRef(version)) return;
  if (!prefetching.has(version)) {
    prefetching.set(
      version,
      Promise.resolve()
        .then(() => prefetchBackendRef(version))
        .finally(() => prefetching.delete(version))
    );
  }
  try {
    await prefetching.get(version);
  } catch (err) {
    log("warn", `Couldn't fetch the backend for ${version} ahead of the restart:`, err && err.message);
  }
}

/** An update finished downloading: fetch its backend, then offer the restart. */
async function _onDownloaded(info) {
  abandonedDownload = false;
  const version = (info && info.version) || state.version;
  const epoch = downloadEpoch;
  if (prefetchBackendRef) {
    // electron-updater has armed install-on-quit; a quit now would skip the checks below.
    setState({ status: STATES.DOWNLOADING, version, progress: 100, preparing: true });
    _applyInstallPolicy();
    await _prefetchBackend(version);
    if (epoch !== downloadEpoch) {
      setState({ preparing: false });
      _applyInstallPolicy();
      log("info", `GAIA ${version} was given up on while its backend was fetched`);
      return;
    }
  }
  setState({
    status: STATES.DOWNLOADED,
    version,
    progress: 100,
    preparing: false,
    error: null,
    lastCheckError: null,
  });
  _applyInstallPolicy();
  log("info", `Update downloaded: ${version}`);

  // The chip offers the restart. Picking a version already asked for one.
  if (restartWhenDownloaded) {
    restartWhenDownloaded = false;
    // The download stays on offer: the chip can restart once this is cleared up.
    pendingRestart = restartToInstall().catch((err) => {
      log("warn", "Restart refused:", err && err.message);
      restartRefusal = err;
    });
    await pendingRestart;
  }
}

/** "Update now": download the update a check found; the chip then offers the restart. */
async function downloadUpdate() {
  if (!autoUpdaterRef) {
    throw new Error("downloadUpdate called before auto-updater was initialised");
  }
  if (state.status !== STATES.AVAILABLE) {
    throw new Error(
      `No update is waiting to download (status: ${state.status}) — check for updates first.`
    );
  }
  if (!_selfInstalls()) throw _manualInstallError(state.version);
  log("info", `User asked to install ${state.version}`);
  installRequested = true;
  _applyInstallPolicy();
  setState({ status: STATES.DOWNLOADING, progress: 0, error: null });
  try {
    await autoUpdaterRef.downloadUpdate();
  } catch (err) {
    const message = (err && err.message) || String(err);
    installRequested = false;
    _applyInstallPolicy();
    setState({ status: STATES.ERROR, error: message });
    throw _userError(
      "downloadFailed",
      { version: state.version || "", reason: message },
      `Downloading GAIA ${state.version || ""} failed: ${message}`
    );
  }
}

/** Restart into a downloaded update, asking first if work would be cut off. */
async function restartToInstall() {
  if (!autoUpdaterRef || state.status !== STATES.DOWNLOADED) {
    throw new Error(`No downloaded update to install (status: ${state.status}).`);
  }
  await _requireInstallSpace();
  _requireNetwork();
  if (!(await confirmRestartRef())) {
    log("info", "Restart put off: work is still running — will install on next quit");
    return;
  }
  log("info", "User chose to restart — calling quitAndInstall");
  _recordInstallAttempt();
  installingNow = true;
  // Silent: the installer wizard would otherwise wait for clicks. Force-run
  // brings the app straight back.
  autoUpdaterRef.quitAndInstall(!wizardNextInstall, true);
}

// ── Core check ───────────────────────────────────────────────────────────────

/**
 * Check for an update. A `background` check that fails only logs and waits for
 * the next one: nobody asked for it, and nothing is wrong with the app.
 */
async function checkForUpdates({ background = false } = {}) {
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
  _refreshPreferences();
  if (state.configError) {
    // An unreadable file may hold a pin; checking anyway could offer past it.
    setState({ status: STATES.ERROR, error: state.configError });
    return;
  }
  checkInProgress = true;
  backgroundCheck = background;
  askedCheck = !background;
  // A routine check may replace the restart the chip offers, never remove it.
  heldDownloadVersion = background && state.status === STATES.DOWNLOADED ? state.version : null;
  if (!heldDownloadVersion) setState({ status: STATES.CHECKING, error: null });
  try {
    // No configured feed: aim at the newest GitHub release on this channel,
    // re-read every check so a new release is found.
    if (!forwardFeedUrl && !state.pinnedVersion && !(await _applyGithubFeed())) {
      return;
    }
    log("info", "Checking for updates...");
    await autoUpdaterRef.checkForUpdates();
  } catch (err) {
    _recordFailure("Update check failed:", err);
  } finally {
    checkInProgress = false;
    backgroundCheck = false;
    heldDownloadVersion = null;
  }
}

/** Show a failure the user asked to hear about; otherwise note it and go idle. */
function _recordFailure(what, err) {
  const message = (err && err.message) || String(err);
  // electron-updater starts an automatic download without awaiting it, so the
  // failure can land before any progress and after the check has returned.
  const autoDownloading =
    !installRequested &&
    !askedCheck &&
    (state.status === STATES.DOWNLOADING ||
      (state.status === STATES.AVAILABLE && state.autoUpdate));
  if (heldDownloadVersion) {
    setState({ status: STATES.DOWNLOADED, version: heldDownloadVersion, lastCheckError: message });
    log("warn", `${what} ${message} — keeping the downloaded update`);
    return;
  }
  if (backgroundCheck || autoDownloading) {
    setState({ status: STATES.IDLE, progress: 0, error: null, lastCheckError: message, preparing: false });
    log("warn", `${what} ${message} — will try again at the next check`);
    return;
  }
  setState({ status: STATES.ERROR, error: message });
  log("error", what, message);
}

function scheduleNextCheck() {
  if (scheduledTimeout) {
    clearTimeout(scheduledTimeout);
    scheduledTimeout = null;
  }
  scheduledTimeout = setTimeout(async () => {
    try {
      await checkForUpdates({ background: true });
    } catch (err) {
      log("error", "Scheduled check threw:", err && err.message);
    }
    scheduleNextCheck();
  }, CHECK_INTERVAL_MS);
}

// ── Event wiring ─────────────────────────────────────────────────────────────

function wireAutoUpdaterEvents() {
  autoUpdaterRef.on("checking-for-update", () => {
    if (!heldDownloadVersion) setState({ status: STATES.CHECKING });
  });

  autoUpdaterRef.on("update-available", (info) => {
    const releaseNotes =
      typeof info.releaseNotes === "string" ? info.releaseNotes : null;
    if (heldDownloadVersion && info.version === heldDownloadVersion) {
      setState({ status: STATES.DOWNLOADED, lastCheckError: null });
      return;
    }
    setState({
      status: STATES.AVAILABLE,
      version: info.version || null,
      releaseNotes,
      downloadUrl: info.version ? _releasePage(info.version) : RELEASES_PAGE,
      error: null,
      lastCheckError: null,
    });
    log("info", `Update available: ${info.version}`);
  });

  autoUpdaterRef.on("update-not-available", (info) => {
    // A release withdrawn since it downloaded must not install on quit.
    if (heldDownloadVersion) _abandonDownload();
    downloadEpoch += 1;
    // Reset to idle so the UI hides any stale "available" chip.
    setState({
      status: STATES.IDLE,
      version: null,
      progress: 0,
      releaseNotes: null,
      downloadUrl: null,
      error: null,
      lastCheckError: null,
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

  autoUpdaterRef.on("update-downloaded", (info) => {
    // Set before anything waits, so installVersion sees the restart it asked for.
    downloadHandled = _onDownloaded(info);
    return downloadHandled;
  });

  autoUpdaterRef.on("error", (err) => {
    _recordFailure("electron-updater error:", err);
  });
}

function registerIpcHandlers() {
  if (ipcHandlersRegistered || !electronApi || !electronApi.ipcMain) return;
  const ipcMain = {
    handle: (channel, fn) =>
      electronApi.ipcMain.handle(channel, async (...args) => {
        try {
          return await fn(...args);
        } catch (err) {
          throw _forRenderer(err);
        }
      }),
  };

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
  ipcMain.handle("gaia:update:revert", async () => {
    await revertUpdate();
    return getState();
  });
  ipcMain.handle("gaia:update:resume", async () => {
    clearPin();
    return getState();
  });
  ipcMain.handle("gaia:update:set-auto-update", async (_event, enabled) => {
    await setAutoUpdate(enabled);
    if (enabled) await checkForUpdates();
    return getState();
  });
  ipcMain.handle("gaia:update:set-include-prereleases", async (_event, enabled) => {
    await setIncludePrereleases(enabled);
    if (enabled) await checkForUpdates();
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
    "gaia:update:revert",
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
 * @param {(version: string) => Promise<void>} [options.prefetchBackend] - Fetch
 *   the backend `version` needs before the restart, so its first launch is offline.
 * @param {(version: string) => boolean} [options.isBackendPrefetched] - Whether
 *   that fetch finished, so a restart no longer needs the internet.
 */
function init(mainWindow, options = {}) {
  if (typeof options.confirmRestart !== "function") {
    throw new TypeError(
      "auto-updater init needs options.confirmRestart, so Restart now can ask before it stops running work"
    );
  }
  mainWindowRef = mainWindow || null;
  confirmRestartRef = options.confirmRestart;
  if (options.prefetchBackend) {
    prefetchBackendRef = options.prefetchBackend;
    isBackendPrefetchedRef = options.isBackendPrefetched;
  }

  // Disabled path — short-circuit before electron-updater, whose singleton
  // throws on access in plain Node tests.
  if (isDisabled()) {
    _initDisabled();
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
  pinUnknown = Boolean(loaded.error);

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
      net: electron.net,
    };
    autoUpdaterRef = electronUpdater.autoUpdater;
    // The Terminal UI may have turned auto-update off since the last check.
    electron.app.on("before-quit", _onBeforeQuit);

    // Record the running version for getState() / listReleases().
    try {
      state.currentVersion = electron.app.getVersion() || null;
    } catch {
      // ignore
    }
    _readInstallOutcome();
    state.previousVersion = previousVersion();
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
    _applyInstallPolicy();
    autoUpdaterRef.disableWebInstaller = true;
    autoUpdaterRef.allowDowngrade = false;

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
  // releases when none is configured. A pin stays on its own release's feed.
  let feedReady = true;
  if (state.pinnedVersion) {
    autoUpdaterRef.setFeedURL(_releaseFeed(state.pinnedVersion));
  } else {
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
      await _seedInstalledBlockmap();
    } catch (err) {
      log("warn", "Couldn't cache the installed version's blockmap; the next update downloads in full:", err && err.message);
    }
    try {
      await checkForUpdates({ background: true });
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

/**
 * Settings still needs the running version and the disabled status, so record
 * the one and answer the status request. Plain Node's require("electron") is a
 * path string, so a missing app or ipcMain leaves the updater as it is.
 */
function _initDisabled() {
  let electron;
  try {
    // eslint-disable-next-line global-require
    electron = require("electron");
  } catch (err) {
    log("warn", "Electron unavailable — Settings can't show the running version:", err && err.message);
    return;
  }
  if (!electron || !electron.app || !electron.ipcMain) return;
  electronApi = { app: electron.app, ipcMain: electron.ipcMain, dialog: electron.dialog, net: electron.net };
  state.currentVersion = electron.app.getVersion() || null;
  if (!ipcHandlersRegistered) {
    electron.ipcMain.handle("gaia:update:get-status", () => getState());
    ipcHandlersRegistered = true;
  }
}

function _onBeforeQuit(event) {
  _refreshPreferences();
  if (state.status !== STATES.DOWNLOADED || !autoUpdaterRef || !autoUpdaterRef.autoInstallOnAppQuit) {
    return;
  }
  // The chip's checks, for an install nobody clicks.
  if ((_isOffline() && !_backendCached()) || _diskTooFullToInstall()) {
    log("info", "Can't finish an install now: leaving the update for a later quit");
    autoUpdaterRef.autoInstallOnAppQuit = false;
    return;
  }
  // The quit guard kept the app open, so nothing is installing.
  if (event && event.defaultPrevented) return;
  _recordInstallAttempt();
}

/** Whether this quit hands over to an update's installer, which replaces the app's files. */
function installsOnQuit() {
  if (!autoUpdaterRef || state.status !== STATES.DOWNLOADED) return false;
  return installingNow || Boolean(autoUpdaterRef.autoInstallOnAppQuit);
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
  if (electronApi) electronApi.app.removeListener("before-quit", _onBeforeQuit);
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
  previousVersion,
  revertUpdate,
  reportStartFailure,
  clearStartFailure,
  clearPin,
  setAutoUpdate,
  setIncludePrereleases,
  downloadUpdate,
  restartToInstall,
  installsOnQuit,
  STATES,
  // For tests: init() runs it on a timer.
  _seedInstalledBlockmap,
};
