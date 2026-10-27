// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What pages in the browser pane may do. Separate from link-policy.cjs, which
 * locks down the app window: the pane is a browser, so any http(s) site loads,
 * but nothing else does, and the app's own backend is out of reach.
 */

"use strict";

const path = require("path");

const PANE_SCHEMES = new Set(["http:", "https:"]);

/** Permissions a pane page gets; everything else (camera, mic, location, notifications, ...) is denied. */
const ALLOWED_PERMISSIONS = new Set(["clipboard-sanitized-write"]);

function isLoopbackHost(hostname) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  return (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host) ||
    host === "0.0.0.0" ||
    host === "[::1]" ||
    host === "[::]" ||
    host === "[::ffff:7f00:1]"
  );
}

/**
 * Hosts an agent may read in the pane with no approval: localhost,
 * *.localhost, 127.0.0.0/8 and [::1]. Python's is_loopback_origin is the
 * same rule; both are tested against tests/fixtures/browser_pane/bridge/loopback.json.
 */
function isApprovalFreeHost(hostname) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  return (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host === "[::1]" ||
    /^127\.(25[0-5]|2[0-4]\d|1?\d?\d)\.(25[0-5]|2[0-4]\d|1?\d?\d)\.(25[0-5]|2[0-4]\d|1?\d?\d)$/.test(host)
  );
}

function portOf(url) {
  if (url.port) return Number(url.port);
  return url.protocol === "https:" || url.protocol === "wss:" ? 443 : 80;
}

/** True for any request (page, fetch, socket) to a loopback port the app itself listens on. */
function isAppServiceUrl(url, blockedPorts) {
  return isLoopbackHost(url.hostname) && blockedPorts.includes(portOf(url));
}

/**
 * @param {string} rawUrl
 * @param {{ blockedPorts: number[] }} ctx Loopback ports the app itself listens on.
 * @returns {{ allowed: true } | { allowed: false, message: string }}
 */
function classifyNavigation(rawUrl, { blockedPorts }) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return { allowed: false, message: `"${rawUrl}" is not a web address.` };
  }
  if (url.protocol === "about:" && url.href === "about:blank") return { allowed: true };
  if (!PANE_SCHEMES.has(url.protocol)) {
    return {
      allowed: false,
      message: `The browser pane opens only http and https pages, not ${url.protocol.replace(/:$/, "")} links.`,
    };
  }
  if (isAppServiceUrl(url, blockedPorts)) {
    return { allowed: false, message: "The browser pane can't open GAIA's own service." };
  }
  return { allowed: true };
}

function isPermissionAllowed(permission) {
  return ALLOWED_PERMISSIONS.has(permission);
}

/**
 * Apply the policy to the pane's session. Call once per session: Electron
 * keeps one session object per partition for the life of the app.
 *
 * @param {Electron.Session} ses
 * @param {{
 *   blockedPorts: () => number[],
 *   onPermissionDenied: (webContents: Electron.WebContents, permission: string, origin: string) => void,
 *   downloadsDir: () => string,
 * }} deps
 */
function installSessionPolicy(ses, { blockedPorts, onPermissionDenied, downloadsDir }) {
  ses.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const allowed = isPermissionAllowed(permission);
    if (!allowed) {
      const origin = originOf(details && details.requestingUrl) || originOf(webContents.getURL());
      onPermissionDenied(webContents, permission, origin);
    }
    callback(allowed);
  });
  ses.setPermissionCheckHandler((_webContents, permission) => isPermissionAllowed(permission));
  ses.setDevicePermissionHandler(() => false);

  // Subframes, fetches and sockets too, not just navigations the tab sees.
  ses.webRequest.onBeforeRequest((details, callback) => {
    let url;
    try {
      url = new URL(details.url);
    } catch {
      callback({ cancel: true });
      return;
    }
    callback({ cancel: isAppServiceUrl(url, blockedPorts()) });
  });

  // Electron shows a native save dialog for any download whose path isn't set; titling it says why it appeared.
  ses.on("will-download", (_event, item) => {
    item.setSaveDialogOptions({
      title: "Save download from the browser pane",
      defaultPath: path.join(downloadsDir(), item.getFilename()),
    });
  });
}

function originOf(rawUrl) {
  try {
    return new URL(rawUrl).origin;
  } catch {
    return "";
  }
}

/**
 * Per-tab rules: popups become pane tabs, and refused navigations are
 * reported on the tab instead of failing without a word.
 *
 * @param {Electron.WebContents} webContents
 * @param {{
 *   blockedPorts: () => number[],
 *   openTab: (url: string) => void,
 *   onBlocked: (url: string, message: string) => void,
 *   stopsAgent?: (url: string) => boolean,
 * }} deps
 */
function attachTabPolicy(webContents, { blockedPorts, openTab, onBlocked, stopsAgent = () => false }) {
  webContents.setWindowOpenHandler(({ url }) => {
    const verdict = classifyNavigation(url, { blockedPorts: blockedPorts() });
    if (verdict.allowed) openTab(url);
    else onBlocked(url, verdict.message);
    return { action: "deny" };
  });
  const guard = (event, url) => {
    const verdict = classifyNavigation(url, { blockedPorts: blockedPorts() });
    if (!verdict.allowed) {
      event.preventDefault();
      onBlocked(url, verdict.message);
    } else if (stopsAgent(url)) {
      event.preventDefault();
    }
  };
  webContents.on("will-navigate", (event) => guard(event, event.url));
  webContents.on("will-redirect", (event) => guard(event, event.url));
}

module.exports = {
  ALLOWED_PERMISSIONS,
  attachTabPolicy,
  classifyNavigation,
  installSessionPolicy,
  isApprovalFreeHost,
  isAppServiceUrl,
  isLoopbackHost,
  isPermissionAllowed,
};
