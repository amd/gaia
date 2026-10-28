// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — the OS folder picker for "give this chat a folder".
 *
 * The renderer invokes `dialog:pick-folder` and gets the chosen absolute path,
 * or null when the user cancels. The web build has no preload, so it keeps
 * GAIA's own picker.
 */

"use strict";

const { BrowserWindow, dialog, ipcMain } = require("electron");

const PICK_FOLDER_CHANNEL = "dialog:pick-folder";

async function pickFolder(event, defaultPath) {
  if (defaultPath !== undefined && typeof defaultPath !== "string") {
    throw new TypeError(`The folder picker's start folder must be a path, got ${typeof defaultPath}.`);
  }
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner) {
    throw new Error("The folder picker was asked for by a page that has no window.");
  }
  const { canceled, filePaths } = await dialog.showOpenDialog(owner, {
    title: "Add a folder to this chat",
    message: "GAIA will be able to read and write everything in the folder you pick, in this chat only.",
    buttonLabel: "Add Folder",
    properties: ["openDirectory", "createDirectory"],
    ...(defaultPath ? { defaultPath } : {}),
  });
  if (canceled) return null;
  if (!filePaths[0]) {
    throw new Error("The folder picker closed without a folder or a cancel.");
  }
  return filePaths[0];
}

function registerIpcHandlers() {
  ipcMain.handle(PICK_FOLDER_CHANNEL, pickFolder);
}

module.exports = { PICK_FOLDER_CHANNEL, registerIpcHandlers };
