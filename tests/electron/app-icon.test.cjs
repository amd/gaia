// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A window without an icon shows Electron's logo in a source build, so every
// window the desktop app opens must take the GAIA icon.

"use strict";

const fs = require("fs");
const path = require("path");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const { appIconPath, appUserModelId } = require(path.join(WEBUI, "services", "app-icon.cjs"));

describe("appUserModelId", () => {
  test("a packaged build matches the installer shortcut's appId", () => {
    const yml = fs.readFileSync(path.join(WEBUI, "electron-builder.yml"), "utf8");
    const appId = yml.match(/^appId:\s*(\S+)/m)[1];
    expect(appUserModelId(true)).toBe(appId);
  });

  test("a source build gets its own ID, apart from the installed app", () => {
    expect(appUserModelId(false)).not.toBe(appUserModelId(true));
  });

  test("main.cjs sets it before the first window", () => {
    const main = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");
    const set = main.indexOf("app.setAppUserModelId(appUserModelId(app.isPackaged))");
    expect(set).toBeGreaterThan(-1);
    expect(set).toBeLessThan(main.indexOf("app.whenReady().then("));
  });
});

describe("appIconPath", () => {
  test.each([
    ["win32", "icon.ico"],
    ["darwin", "icon.png"],
    ["linux", "icon.png"],
  ])("points %s at an asset that exists (%s)", (platform, file) => {
    const icon = appIconPath(platform);
    expect(path.basename(icon)).toBe(file);
    expect(fs.existsSync(icon)).toBe(true);
  });
});

describe("every BrowserWindow takes the GAIA icon", () => {
  const sources = [
    path.join(WEBUI, "main.cjs"),
    ...fs
      .readdirSync(path.join(WEBUI, "services"))
      .filter((f) => f.endsWith(".cjs"))
      .map((f) => path.join(WEBUI, "services", f)),
  ];

  const windows = sources.flatMap((file) => {
    const text = fs.readFileSync(file, "utf8");
    const found = [];
    const re = /new BrowserWindow\(\{/g;
    let m;
    while ((m = re.exec(text))) {
      // The options object runs to its matching close brace.
      let depth = 0;
      let end = m.index + m[0].length - 1;
      for (; end < text.length; end++) {
        if (text[end] === "{") depth++;
        else if (text[end] === "}" && --depth === 0) break;
      }
      found.push([`${path.basename(file)}@${m.index}`, text.slice(m.index, end + 1)]);
    }
    return found;
  });

  test("finds the app's windows", () => {
    expect(windows.length).toBeGreaterThanOrEqual(2);
  });

  test.each(windows)("%s passes icon: appIconPath()", (_where, options) => {
    expect(options).toMatch(/\bicon:\s*appIconPath\(\)/);
  });
});
