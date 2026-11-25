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

// U+2068/U+2069 keep a URL left-to-right inside Arabic text.
const isolate = (text) => `⁨${text}⁩`;

const NOTICE_TEXT = {
  en: {
    blockedTitle: "Link blocked",
    notOpenedTitle: "Link not opened",
    ok: "OK",
    noScheme: "no scheme",
    blocked: (url, scheme) =>
      `GAIA refused to open "${url}" (${scheme}). ` +
      "Only http, https and mailto links open outside the app — a link that " +
      "resolves to a local file or network share is never launched.",
    openFailed: (url, reason) =>
      `GAIA couldn't open "${url}": ${reason}. ` +
      "Check that this PC has an app set up for that kind of link.",
  },
  ar: {
    blockedTitle: "تم حظر الرابط",
    notOpenedTitle: "لم يُفتح الرابط",
    ok: "حسنًا",
    noScheme: "بلا بروتوكول",
    blocked: (url, scheme) =>
      `رفض GAIA فتح "${isolate(url)}" (${isolate(scheme)}). ` +
      "لا تُفتح خارج التطبيق إلا روابط http وhttps وmailto، ولا يُشغَّل أبدًا " +
      "رابط يشير إلى ملف محلي أو مجلد مشترك على الشبكة.",
    openFailed: (url, reason) =>
      `تعذّر على GAIA فتح "${isolate(url)}": ${isolate(reason)}. ` +
      "تأكد من وجود تطبيق على هذا الجهاز مُعدّ لفتح هذا النوع من الروابط.",
  },
};

/** The link-notice strings for an OS locale such as "ar-EG"; English for any language without them. */
function linkNoticeText(locale) {
  const lang = String(locale || "").toLowerCase().split(/[-_]/)[0];
  return NOTICE_TEXT[lang] || NOTICE_TEXT.en;
}

/**
 * A one-line, user-facing reason a URL was refused. Never silently dropped —
 * the caller logs this and shows it, so a blocked click is explainable.
 *
 * @param {string} rawUrl
 * @param {string} [locale] The OS locale, as `app.getLocale()` gives it.
 * @returns {string}
 */
function describeBlockedUrl(rawUrl, locale) {
  const text = linkNoticeText(locale);
  const shown = typeof rawUrl === "string" && rawUrl ? rawUrl : String(rawUrl);
  let scheme = text.noScheme;
  try {
    scheme = new URL(shown).protocol.replace(/:$/, "");
  } catch {
    /* keep the "no scheme" label */
  }
  return text.blocked(shown.slice(0, 300), scheme);
}

/**
 * A one-line, user-facing reason the OS could not open an allowed link.
 *
 * @param {string} rawUrl
 * @param {unknown} err
 * @param {string} [locale] The OS locale, as `app.getLocale()` gives it.
 * @returns {string}
 */
function describeOpenFailure(rawUrl, err, locale) {
  const reason = (err && err.message ? err.message : String(err)).replace(
    /\.+$/,
    "",
  );
  return linkNoticeText(locale).openFailed(String(rawUrl).slice(0, 300), reason);
}

/**
 * `shell.openExternal` rejects when the OS has no handler (a mailto: link on a
 * PC with no mail app). Unhandled, that rejection reaches the process-level
 * fatal handler and quits the app, so every caller goes through here.
 *
 * @param {{ openExternal: (url: string) => Promise<void> }} shell
 * @param {string} url
 * @param {(err: unknown) => void} onFailure
 * @returns {Promise<void>}
 */
function openExternalSafely(shell, url, onFailure) {
  return Promise.resolve()
    .then(() => shell.openExternal(url))
    .catch(onFailure);
}

module.exports = {
  ALLOWED_EXTERNAL_SCHEMES,
  isAllowedExternalUrl,
  isInAppNavigation,
  describeBlockedUrl,
  describeOpenFailure,
  openExternalSafely,
  linkNoticeText,
};
