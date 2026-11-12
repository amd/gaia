// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Which web permissions the app's page gets.
 *
 * Electron grants every permission by default. This grants `media` with audio
 * only (dictation and voice mode), plus the few the UI already relies on, to the
 * app's own page (the bundled file:// document, or the Vite dev server when
 * unpackaged), and denies everything else and every other origin.
 */

const MIC_SETTINGS_CHANNEL = "system:open-mic-settings";

/** Used by the UI today: Copy buttons, chat alerts, and keeping the screen on in voice mode. */
const OTHER_ALLOWED = new Set(["clipboard-sanitized-write", "notifications", "fullscreen", "screen-wake-lock"]);

/** Whether `url` is the app's own page. */
function isOwnPage(url, { isPackaged }) {
  if (typeof url !== "string" || !url) return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol === "file:") return true;
  return !isPackaged && parsed.protocol === "http:" && ["localhost", "127.0.0.1"].includes(parsed.hostname) && parsed.port === "5174";
}

/** Grant an audio-only media request, or one of OTHER_ALLOWED, from the app's own page. */
function allowRequest(permission, details, opts) {
  if (OTHER_ALLOWED.has(permission)) return isOwnPage((details && details.requestingUrl) || "", opts);
  if (permission !== "media") return false;
  const types = Array.isArray(details && details.mediaTypes) ? details.mediaTypes : [];
  if (types.length === 0 || types.some((t) => t !== "audio")) return false;
  return isOwnPage((details && (details.requestingUrl || details.securityOrigin)) || "", opts);
}

/** Permission checks carry no media types; `media` checks are answered for audio by the request handler. */
function allowCheck(permission, requestingOrigin, details, opts) {
  if (OTHER_ALLOWED.has(permission)) return isOwnPage(requestingOrigin || (details && details.requestingUrl) || "", opts);
  if (permission !== "media") return false;
  if (details && details.mediaType && details.mediaType !== "audio") return false;
  return isOwnPage(requestingOrigin || (details && details.requestingUrl) || "", opts);
}

/** The OS pane where a person allows the microphone, or null where there is none to open. */
function micSettingsUrl(platform) {
  if (platform === "win32") return "ms-settings:privacy-microphone";
  if (platform === "darwin") return "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone";
  return null;
}

function installMediaPermissions({ session, ipcMain, shell, isPackaged, platform }) {
  const opts = { isPackaged };
  session.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    callback(allowRequest(permission, details, opts));
  });
  session.setPermissionCheckHandler((_webContents, permission, requestingOrigin, details) =>
    allowCheck(permission, requestingOrigin, details, opts),
  );
  ipcMain.handle(MIC_SETTINGS_CHANNEL, async () => {
    const url = micSettingsUrl(platform);
    if (!url) return false;
    await shell.openExternal(url);
    return true;
  });
}

module.exports = { installMediaPermissions, allowRequest, allowCheck, micSettingsUrl, isOwnPage, MIC_SETTINGS_CHANNEL };
