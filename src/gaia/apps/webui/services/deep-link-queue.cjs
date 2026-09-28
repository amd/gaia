// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * deep-link-queue.cjs — hold gaia:// links until the backend can verify them.
 *
 * A cold-start "Open in GAIA" link reaches the app long before the backend
 * listens, and the install gate resolves the agent through the backend's
 * catalog API (failing closed). This queue holds every link that arrives
 * before startup has waited for the backend, then dispatches each exactly
 * once. Electron-free so the ordering can be unit-tested.
 */

"use strict";

/**
 * @param {object} deps
 * @param {(rawUrl: string) => void} deps.dispatch Act on one validated link.
 * @param {(rawUrls: string[]) => void} deps.reportNotReady Tell the user the
 *   queued links could not be opened because the backend never came up.
 * @param {{log: Function}} [deps.logger]
 */
function createStartupDeepLinkQueue({ dispatch, reportNotReady, logger = console }) {
  if (typeof dispatch !== "function" || typeof reportNotReady !== "function") {
    throw new Error("createStartupDeepLinkQueue requires dispatch and reportNotReady functions");
  }
  const pending = [];
  let drained = false;

  return {
    /**
     * Accept a validated link: queue it until drain(), dispatch it after.
     * @returns {"queued" | "duplicate" | "dispatched"}
     */
    accept(rawUrl) {
      if (drained) {
        dispatch(rawUrl);
        return "dispatched";
      }
      if (pending.includes(rawUrl)) return "duplicate";
      pending.push(rawUrl);
      logger.log(`[deep-link] Queued until the backend is ready: ${rawUrl}`);
      return "queued";
    },

    /**
     * Release the queue once, after startup has waited for the backend.
     * Later calls are no-ops, so nothing is dispatched twice.
     *
     * @param {{backendReady: boolean, argvUrl?: string|null}} opts
     * @returns {string[]} The links dispatched by this call.
     */
    drain({ backendReady, argvUrl = null }) {
      if (drained) return [];
      drained = true;
      const urls = pending.splice(0);
      if (argvUrl && !urls.includes(argvUrl)) urls.push(argvUrl);
      if (urls.length === 0) return [];
      if (!backendReady) {
        reportNotReady(urls);
        return [];
      }
      for (const url of urls) dispatch(url);
      return urls;
    },

    get isDrained() {
      return drained;
    },

    get pendingCount() {
      return pending.length;
    },
  };
}

module.exports = { createStartupDeepLinkQueue };
