// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Deleting the macOS app or a Linux AppImage runs no uninstall step, so the
// daemon watches the path the packaged app hands its backend (#6682).

"use strict";

const fs = require("fs");
const path = require("path");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");

function backendSpawnEnv() {
  const start = main.indexOf('["chat", "--ui", "--ui-port", String(backendPort)]');
  expect(start).toBeGreaterThan(-1);
  const end = main.indexOf("detached: false", start);
  expect(end).toBeGreaterThan(start);
  return main.slice(start, end);
}

describe("the backend learns which app started it", () => {
  test("a packaged app passes GAIA_APP_PATH", () => {
    const env = backendSpawnEnv();
    const packaged = env.indexOf("...(app.isPackaged && {");
    const appPath = env.indexOf("GAIA_APP_PATH:");
    expect(packaged).toBeGreaterThan(-1);
    expect(appPath).toBeGreaterThan(packaged);
  });

  test("an AppImage passes the image file, not its temporary mount", () => {
    expect(backendSpawnEnv()).toContain(
      'GAIA_APP_PATH: process.env.APPIMAGE || app.getPath("exe")'
    );
  });

  test("a source build passes none, so a dev daemon is not tied to Electron", () => {
    const env = backendSpawnEnv();
    const before = env.slice(0, env.indexOf("...(app.isPackaged && {"));
    expect(before).not.toContain("GAIA_APP_PATH");
  });
});
