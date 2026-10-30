// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contract for main.cjs (it cannot be required without
 * Electron): its dialogs have an owner window and a title, so they can't open
 * hidden behind other windows or named after a blob: URL.
 */

const fs = require("fs");
const path = require("path");

const main = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
  "utf8"
);

function body(name) {
  const start = main.indexOf(`async function ${name}(`);
  expect(start).toBeGreaterThan(-1);
  return main.slice(start, main.indexOf("\n}\n", start));
}

test("a download's Save dialog is titled, not named after its blob: URL", () => {
  expect(main).toMatch(/session\.defaultSession\.on\("will-download"[\s\S]{0,200}setSaveDialogOptions\(\{\s*title: "Save from GAIA"/);
});

test("the will-download listener is added once, not on every window reopen", () => {
  expect(main.match(/"will-download"/g)).toHaveLength(1);
  const start = main.indexOf("function createWindow(");
  expect(start).toBeGreaterThan(-1);
  const rest = main.slice(start);
  const createWindow = rest.slice(0, rest.search(/\r?\n\}\r?\n/));
  expect(createWindow).not.toMatch(/will-download|titleDownloadSaveDialogs/);
  expect(main.match(/titleDownloadSaveDialogs\(\);/g)).toHaveLength(1);
});

test("the backend-crash dialog is shown with the main window as its owner", () => {
  const fn = body("handleBackendCrash");
  expect(fn).toMatch(/showBackendCrashDialog\(\{[\s\S]*parent: mainWindow/);
});
