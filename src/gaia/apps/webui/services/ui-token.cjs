// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The Agent UI API token, read from the file the backend writes.
 *
 * The backend refuses every /api and /v1 request without it (GET /api/health
 * aside). Path rules mirror `token_path()` in src/gaia/ui/api_token.py:
 * GAIA_UI_TOKEN_FILE, else <gaia_home>/agent-ui-token, where gaia_home is the
 * first non-empty of GAIA_HOME, GAIA_CONFIG_DIR, else ~/.gaia.
 *
 * The desktop app adds it as `Authorization: Bearer` to the requests its
 * window makes to the backend it spawned (installBackendAuth) and to its own
 * Node-side calls (authHeaders). The renderer never sees it.
 */

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");

const TOKEN_FILE_NAME = "agent-ui-token";
const ENV_TOKEN_FILE = "GAIA_UI_TOKEN_FILE";
const REJECTION_CODE = "ui_token_required";

/** Python's os.path.expanduser: a leading `~` alone or before a separator. */
function expandUser(p, homedir) {
  if (p === "~") return homedir;
  if (/^~[\\/]/.test(p)) return path.join(homedir, p.slice(2));
  return p;
}

/** Python's os.path.expandvars: unknown variables stay as written. */
function expandVars(p, env, platform) {
  const lookup = (name, whole) => (env[name] !== undefined ? env[name] : whole);
  let out = p.replace(/\$\{([^}]+)\}|\$(\w+)/g, (whole, braced, bare) => lookup(braced || bare, whole));
  if (platform === "win32") out = out.replace(/%([^%]+)%/g, (whole, name) => lookup(name, whole));
  return out;
}

function expand(raw, { env, homedir, platform }) {
  return expandVars(expandUser(raw, homedir), env, platform);
}

/** GAIA's home folder, as gaia.home.gaia_home() resolves it. */
function gaiaHome({ env = process.env, homedir = os.homedir(), platform = process.platform } = {}) {
  for (const name of ["GAIA_HOME", "GAIA_CONFIG_DIR"]) {
    const raw = (env[name] || "").trim();
    if (raw) return expand(raw, { env, homedir, platform });
  }
  return path.join(homedir, ".gaia");
}

/** Where the backend keeps the token. */
function uiTokenPath({ env = process.env, homedir = os.homedir(), platform = process.platform } = {}) {
  const override = (env[ENV_TOKEN_FILE] || "").trim();
  if (override) return expand(override, { env, homedir, platform });
  return path.join(gaiaHome({ env, homedir, platform }), TOKEN_FILE_NAME);
}

/**
 * A cached reader. `get()` is null while the file is missing (the backend
 * has not created it yet); any other read failure throws.
 */
function createUiTokenReader({ resolvePath = () => uiTokenPath(), readFile = fs.readFileSync } = {}) {
  let cached = null;
  return {
    path: resolvePath,
    get() {
      if (cached) return cached;
      const file = resolvePath();
      let text;
      try {
        text = readFile(file, "utf8");
      } catch (err) {
        if (err && err.code === "ENOENT") return null;
        throw new Error(`Cannot read the GAIA Agent UI token at ${file}: ${err.message}`, { cause: err });
      }
      cached = String(text).trim() || null;
      return cached;
    },
    /** Forget the cached token, so a rotated one is read on the next request. */
    clear() {
      cached = null;
    },
  };
}

const defaultReader = createUiTokenReader();

/** `Authorization` for a backend request, or {} while there is no token yet. */
function authHeaders(reader = defaultReader) {
  const token = reader.get();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** True when `url` is on the backend this app spawned, and nowhere else. */
function isBackendUrl(url, port) {
  if (!port) return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return (
    parsed.protocol === "http:" &&
    (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost") &&
    parsed.port === String(port)
  );
}

/**
 * Add the bearer token to every request the app's pages make to its backend:
 * fetch, EventSource, <img>, <a> downloads. Electron keeps one listener per
 * webRequest event per session, so this owns onBeforeSendHeaders and
 * onCompleted on `session`; nothing else in the app registers either.
 *
 * @param {Electron.Session} session
 * @param {{getPort: () => number|null, reader?: ReturnType<typeof createUiTokenReader>, log?: Console}} options
 */
function installBackendAuth(session, { getPort, reader = defaultReader, log = console }) {
  session.webRequest.onBeforeSendHeaders((details, callback) => {
    if (!isBackendUrl(details.url, getPort())) {
      callback({ requestHeaders: details.requestHeaders });
      return;
    }
    let token = null;
    try {
      token = reader.get();
    } catch (err) {
      log.error(`[ui-token] ${err.message}`);
    }
    const requestHeaders = { ...details.requestHeaders };
    if (token) requestHeaders.Authorization = `Bearer ${token}`;
    callback({ requestHeaders });
  });
  // A 401 can mean the token file was replaced since it was cached.
  session.webRequest.onCompleted((details) => {
    if (details.statusCode === 401 && isBackendUrl(details.url, getPort())) reader.clear();
  });
}

module.exports = {
  ENV_TOKEN_FILE,
  REJECTION_CODE,
  TOKEN_FILE_NAME,
  authHeaders,
  createUiTokenReader,
  defaultReader,
  gaiaHome,
  installBackendAuth,
  isBackendUrl,
  uiTokenPath,
};
