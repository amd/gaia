// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GNOME and KDE tie a running window to its launcher by app_id / WM_CLASS.
// Electron takes that from package.json's desktopName, so it has to name the
// .desktop file electron-builder installs, or the dock shows GAIA as an
// unknown window and "Click GAIA in the dock" leads nowhere.

const fs = require("fs");
const path = require("path");

const webui = path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const pkg = JSON.parse(fs.readFileSync(path.join(webui, "package.json"), "utf8"));
const builder = fs.readFileSync(path.join(webui, "electron-builder.yml"), "utf8");

function yamlValue(key) {
  const match = builder.match(new RegExp(String.raw`^[ \t]*${key}:[ \t]*([^\s#]+)`, "m"));
  if (!match) throw new Error(`electron-builder.yml has no ${key}`);
  return match[1];
}

test("the window's app_id matches the installed .desktop file and its StartupWMClass", () => {
  expect(pkg.desktopName).toMatch(/\.desktop$/);
  const appId = pkg.desktopName.replace(/\.desktop$/, "");
  // Without linux.syncDesktopName, the .desktop file is named after executableName.
  expect(builder).not.toMatch(/^\s*syncDesktopName:/m);
  expect(yamlValue("executableName")).toBe(appId);
  expect(yamlValue("StartupWMClass")).toBe(appId);
});
