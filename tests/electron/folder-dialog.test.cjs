// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The OS folder picker behind "give this chat a folder" (services/folder-dialog.cjs). */

"use strict";

const fs = require("fs");
const path = require("path");

const { BrowserWindow, dialog, ipcMain } = require("electron"); // mocked via moduleNameMapper

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const { PICK_FOLDER_CHANNEL, registerIpcHandlers } = require(path.join(WEBUI, "services", "folder-dialog.cjs"));

const owner = { id: "main-window" };

beforeEach(() => {
  BrowserWindow.fromWebContents = jest.fn(() => owner);
  dialog.showOpenDialog.mockReset();
  registerIpcHandlers();
});

afterEach(() => {
  ipcMain.removeHandler(PICK_FOLDER_CHANNEL);
  delete BrowserWindow.fromWebContents;
});

test("returns the folder chosen in a dialog owned by the asking window", async () => {
  dialog.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: ["/Users/me/notes"] });

  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL, "/Users/me")).resolves.toBe("/Users/me/notes");

  const [parent, options] = dialog.showOpenDialog.mock.calls[0];
  expect(parent).toBe(owner);
  expect(options.properties).toEqual(["openDirectory", "createDirectory"]);
  expect(options.defaultPath).toBe("/Users/me");
});

test("returns null when the user cancels", async () => {
  dialog.showOpenDialog.mockResolvedValue({ canceled: true, filePaths: [] });
  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL)).resolves.toBeNull();
  expect(dialog.showOpenDialog.mock.calls[0][1]).not.toHaveProperty("defaultPath");
});

test("fails loudly when the dialog returns no folder and no cancel", async () => {
  dialog.showOpenDialog.mockResolvedValue({ canceled: false, filePaths: [] });
  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL)).rejects.toThrow(/without a folder/);
});

test("refuses a start folder that isn't a path", async () => {
  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL, 42)).rejects.toThrow(/must be a path/);
  expect(dialog.showOpenDialog).not.toHaveBeenCalled();
});

test("refuses a request from a page with no window", async () => {
  BrowserWindow.fromWebContents = jest.fn(() => null);
  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL)).rejects.toThrow(/no window/);
  expect(dialog.showOpenDialog).not.toHaveBeenCalled();
});

test("dialog errors reach the renderer", async () => {
  dialog.showOpenDialog.mockRejectedValue(new Error("dialog broke"));
  await expect(ipcMain.simulateInvoke(PICK_FOLDER_CHANNEL)).rejects.toThrow("dialog broke");
});

describe("wiring", () => {
  test("preload.cjs exposes the channel the service registers, passing the start folder", () => {
    const exposed = {};
    const invoke = jest.fn();
    jest.isolateModules(() => {
      jest.doMock("electron", () => ({
        contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
        ipcRenderer: { invoke, on: jest.fn(), removeListener: jest.fn(), send: jest.fn() },
      }));
      require(path.join(WEBUI, "preload.cjs"));
    });
    exposed.gaiaAPI.dialog.pickFolder("/Users/me");
    expect(invoke).toHaveBeenCalledWith(PICK_FOLDER_CHANNEL, "/Users/me");
  });

  test("main.cjs registers the handler with the other services", () => {
    const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");
    expect(main).toMatch(/require\("\.\/services\/folder-dialog\.cjs"\)/);
    const start = main.indexOf("function initializeServices(");
    const init = main.slice(start, main.indexOf("\n}\n", start));
    expect(init).toMatch(/folderDialog\.registerIpcHandlers\(\);/);
  });
});
