// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const path = require("path");

/**
 * The GAIA window icon. Every BrowserWindow must pass it: a source build runs
 * as electron.exe, so a window without one shows Electron's logo.
 *
 * @param {string} [platform] Defaults to process.platform.
 * @returns {string} Absolute path to the icon asset.
 */
function appIconPath(platform = process.platform) {
  return path.join(__dirname, "..", "assets", platform === "win32" ? "icon.ico" : "icon.png");
}

/**
 * The Windows AppUserModelID. Without one, the taskbar groups GAIA with every
 * other window of the same electron.exe and shows Electron's icon. Packaged
 * builds match the installer's shortcut (`appId` in electron-builder.yml).
 *
 * @param {boolean} isPackaged app.isPackaged
 * @returns {string}
 */
function appUserModelId(isPackaged) {
  return isPackaged ? "ai.amd.gaia" : "ai.amd.gaia.dev";
}

module.exports = { appIconPath, appUserModelId };
