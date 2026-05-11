// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * index-query.cjs — Build the `?api=` query object that main.cjs hands
 * to BrowserWindow.loadFile() so the renderer can reach the random
 * backend port that PortManager picked.
 *
 * Extracted from main.cjs (issue #851) so it can be unit-tested without
 * an Electron runtime. Pure CommonJS, no Node built-ins required, no
 * Electron imports.
 *
 * Contract: this module must stay in sync with the TRUSTED_API_RE
 * allowlist in src/gaia/apps/webui/src/utils/apiBase.ts. The renderer
 * rejects any value that does not match that regex, so the URL produced
 * here MUST satisfy `/^https?:\/\/(127\.0\.0\.1|localhost):\d+\/api$/`.
 *
 * The cross-file invariant is enforced by tests/electron/test_loadapp_query.mjs.
 */

"use strict";

/** Path the backend serves its API under. Shared so a future change is one edit. */
const API_PATH = "/api";

/**
 * Build the loadFile query object for a given backend port.
 *
 * @param {number} port - Integer in (0, 65535]. Typically chosen by
 *   PortManager.findFreePort(); falls back to DEFAULT_BACKEND_PORT (4200)
 *   in main.cjs if findFreePort fails.
 * @returns {{api: string}} - Object passed as `loadFile(..., { query })`.
 *   Electron URL-encodes the values; the renderer reads them back via
 *   `URLSearchParams(window.location.search).get('api')`.
 * @throws {Error} - If port is not a valid integer in (0, 65535].
 */
function buildIndexQuery(port) {
  if (
    typeof port !== "number" ||
    !Number.isInteger(port) ||
    port <= 0 ||
    port > 65535
  ) {
    throw new Error(`buildIndexQuery: invalid port ${port}`);
  }
  return { api: `http://127.0.0.1:${port}${API_PATH}` };
}

module.exports = { buildIndexQuery, API_PATH };
