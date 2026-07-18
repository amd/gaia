// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * deep-link.cjs — parse `gaia://` deep links (issue #1725).
 *
 * The website's "Open in GAIA" button hands the running app a
 * `gaia://hub/install/<id>` URL. This module is the pure, Electron-free
 * parser so it can be unit-tested without a running app; main.cjs owns the
 * OS protocol registration and dispatch to the install runtime.
 *
 * Design: parse errors are THROWN with an actionable message (no silent
 * fallback). main.cjs surfaces the message to the user instead of dropping a
 * malformed link on the floor.
 */

"use strict";

const SCHEME = "gaia:";

// Agent ids as published on the hub: start alphanumeric, then letters, digits,
// dot, dash, underscore. Bounded length keeps a hostile link from ballooning.
const AGENT_ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

/**
 * Parse a `gaia://` deep link into a structured command.
 *
 * Supported form:
 *   gaia://hub/install/<id>  → { action: "install", agentId }
 *
 * @param {string} rawUrl
 * @returns {{ action: "install", agentId: string }}
 * @throws {Error} with an actionable message on anything malformed/unsupported
 */
function parseDeepLink(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) {
    throw new Error(
      'Not a GAIA deep link — expected a "gaia://…" URL but got ' +
        (rawUrl === "" ? "an empty string" : JSON.stringify(rawUrl)) +
        "."
    );
  }

  const trimmed = rawUrl.trim();
  let parsed;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error(`Malformed GAIA deep link "${trimmed}" — not a valid URL.`);
  }

  if (parsed.protocol !== SCHEME) {
    throw new Error(
      `Unsupported URL scheme "${parsed.protocol}" — GAIA deep links must start with "gaia://".`
    );
  }

  const host = parsed.hostname;
  const segments = parsed.pathname.split("/").filter(Boolean);

  if (host === "hub" && segments[0] === "install") {
    const agentId = segments[1] ? decodeURIComponent(segments[1]) : "";
    if (!agentId) {
      throw new Error(
        `GAIA install link "${trimmed}" is missing an agent id — expected gaia://hub/install/<id>.`
      );
    }
    if (!AGENT_ID_RE.test(agentId)) {
      throw new Error(
        `GAIA install link has an invalid agent id "${agentId}" — ids may contain letters, digits, dot, dash and underscore.`
      );
    }
    if (segments.length > 2) {
      throw new Error(
        `GAIA install link "${trimmed}" has unexpected extra path segments — expected gaia://hub/install/<id>.`
      );
    }
    return { action: "install", agentId };
  }

  throw new Error(
    `Unrecognized GAIA deep link "${trimmed}" — only gaia://hub/install/<id> is supported.`
  );
}

/**
 * Find the first `gaia://` argument in an argv array (Windows/Linux hand the
 * deep link to the app as a command-line argument). Returns null if absent.
 *
 * @param {string[]} argv
 * @returns {string | null}
 */
function extractDeepLinkFromArgv(argv) {
  if (!Array.isArray(argv)) return null;
  for (const arg of argv) {
    if (typeof arg === "string" && arg.startsWith("gaia://")) return arg;
  }
  return null;
}

module.exports = { parseDeepLink, extractDeepLinkFromArgv };
