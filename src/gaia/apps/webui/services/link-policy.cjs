// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * link-policy.cjs — which URLs the shell is allowed to hand to the OS.
 *
 * The renderer is loaded from `file://`, so a scheme-less link in
 * LLM-rendered markdown (`/Windows/System32/calc.exe`, `//host/share/x.exe`)
 * RESOLVES to `file:///C:/…` / `file://host/…` before Electron hands it to
 * `setWindowOpenHandler`. Passing that to `shell.openExternal` is a
 * ShellExecute on a local binary or an SMB fetch. The check therefore runs on
 * the resolved URL and allows only web/mail schemes.
 *
 * Electron-free so it can be unit-tested without a running app; main.cjs owns
 * the window handlers that call it.
 */

"use strict";

/** The only schemes `shell.openExternal` may ever receive. */
const ALLOWED_EXTERNAL_SCHEMES = new Set(["http:", "https:", "mailto:"]);

/**
 * @param {string} rawUrl A URL already resolved by Electron/Chromium.
 * @returns {boolean} True when it is safe to hand to `shell.openExternal`.
 */
function isAllowedExternalUrl(rawUrl) {
  if (typeof rawUrl !== "string" || rawUrl === "") return false;
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false; // Unresolvable/relative — never openExternal it.
  }
  return ALLOWED_EXTERNAL_SCHEMES.has(parsed.protocol.toLowerCase());
}

/**
 * True when `targetUrl` is the document already loaded, differing only by
 * fragment or query — i.e. an in-app anchor, not a navigation away.
 *
 * @param {string} targetUrl
 * @param {string} currentUrl
 * @returns {boolean}
 */
function isInAppNavigation(targetUrl, currentUrl) {
  if (typeof targetUrl !== "string" || typeof currentUrl !== "string") return false;
  let target;
  let current;
  try {
    target = new URL(targetUrl);
    current = new URL(currentUrl);
  } catch {
    return false;
  }
  return (
    target.protocol === current.protocol &&
    target.host === current.host &&
    target.pathname === current.pathname
  );
}

/**
 * A one-line, user-facing reason a URL was refused. Never silently dropped —
 * the caller logs this and shows it, so a blocked click is explainable.
 *
 * @param {string} rawUrl
 * @returns {string}
 */
function describeBlockedUrl(rawUrl) {
  const shown = typeof rawUrl === "string" && rawUrl ? rawUrl : String(rawUrl);
  let scheme = "no scheme";
  try {
    scheme = new URL(shown).protocol.replace(/:$/, "");
  } catch {
    /* keep "no scheme" */
  }
  return (
    `GAIA refused to open "${shown.slice(0, 300)}" (${scheme}). ` +
    "Only http, https and mailto links open outside the app — a link that " +
    "resolves to a local file or network share is never launched."
  );
}

module.exports = {
  ALLOWED_EXTERNAL_SCHEMES,
  isAllowedExternalUrl,
  isInAppNavigation,
  describeBlockedUrl,
};
