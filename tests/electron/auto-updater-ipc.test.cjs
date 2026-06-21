// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * IPC contract test: the channel strings in preload.cjs (gaiaUpdater) must
 * be a subset of the channels registered by auto-updater.cjs (ipcMain.handle).
 *
 * This is a static analysis test — it parses the source files as text rather
 * than executing them, so it stays fast and doesn't require a running Electron.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const PRELOAD_PATH = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/preload.cjs"
);
const AUTO_UPDATER_PATH = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/auto-updater.cjs"
);

function extractIpcChannels(src, pattern) {
  const channels = [];
  const re = new RegExp(pattern, "g");
  let m;
  while ((m = re.exec(src)) !== null) {
    channels.push(m[1]);
  }
  return channels;
}

describe("IPC contract: preload ⊆ auto-updater handlers", () => {
  let preloadSrc;
  let autoUpdaterSrc;

  beforeAll(() => {
    preloadSrc = fs.readFileSync(PRELOAD_PATH, "utf8");
    autoUpdaterSrc = fs.readFileSync(AUTO_UPDATER_PATH, "utf8");
  });

  test("all gaia:update:* channels in preload.cjs are handled by auto-updater.cjs", () => {
    // Extract ipcRenderer.invoke("channel") strings from preload.cjs
    const invokeChannels = extractIpcChannels(
      preloadSrc,
      'ipcRenderer\\.invoke\\("([^"]+)"'
    ).filter((ch) => ch.startsWith("gaia:update:"));

    expect(invokeChannels.length).toBeGreaterThan(0);

    // Extract ipcMain.handle("channel") strings from auto-updater.cjs
    const handlerChannels = extractIpcChannels(
      autoUpdaterSrc,
      'ipcMain\\.handle\\("([^"]+)"'
    );

    for (const ch of invokeChannels) {
      expect(handlerChannels).toContain(ch);
    }
  });

  test("preload exposes listReleases, installVersion, resumeUpdates", () => {
    expect(preloadSrc).toContain("listReleases");
    expect(preloadSrc).toContain("installVersion");
    expect(preloadSrc).toContain("resumeUpdates");
  });

  test("auto-updater handles gaia:update:list-releases", () => {
    expect(autoUpdaterSrc).toContain('"gaia:update:list-releases"');
  });

  test("auto-updater handles gaia:update:install-version", () => {
    expect(autoUpdaterSrc).toContain('"gaia:update:install-version"');
  });

  test("auto-updater handles gaia:update:resume", () => {
    expect(autoUpdaterSrc).toContain('"gaia:update:resume"');
  });
});
