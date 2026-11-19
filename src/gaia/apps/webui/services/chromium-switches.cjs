// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Chromium switches main.cjs appends before app.whenReady(). Pure so the
 * per-platform and per-env choices are testable without launching Electron.
 */

const TRUTHY = new Set(["1", "true", "yes"]);

function gpuDisabled(env) {
  return TRUTHY.has(String(env.GAIA_DISABLE_GPU || "").trim().toLowerCase());
}

/** @returns {Array<[string, string?]>} switch name and optional value, in append order */
function chromiumSwitches({ platform, env }) {
  const switches = [];
  if (platform === "linux") {
    // chrome-sandbox is stripped by after-pack.cjs, and direct ./AppImage runs skip the .desktop flag (#782).
    switches.push(["no-sandbox"]);
    // Partial wp_color_manager support in some Wayland compositors SIGTRAPs Chromium.
    switches.push(["disable-features", "WaylandColorManagement"]);
  }
  // Opt-in escape hatch for GPU stacks whose GPU process aborts at startup (#999).
  if (gpuDisabled(env)) switches.push(["disable-gpu"]);
  return switches;
}

module.exports = { chromiumSwitches, gpuDisabled };
