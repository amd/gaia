// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Tests for the main window's per-platform frame (services/window-frame.cjs). */

const fs = require("fs");
const path = require("path");

const WEBUI = path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const {
  TITLE_BAR_HEIGHT,
  titleBarKind,
  titleBarOptions,
} = require(path.join(WEBUI, "services", "window-frame.cjs"));

describe("titleBarOptions", () => {
  test("Linux keeps the system frame, so GNOME Wayland still gets window controls", () => {
    expect(titleBarOptions("linux")).toEqual({});
  });

  test("Windows hides the title bar and draws the controls as an overlay", () => {
    expect(titleBarOptions("win32")).toEqual({
      titleBarStyle: "hidden",
      titleBarOverlay: { color: "#1A1917", symbolColor: "#EFECE6", height: 48 },
    });
  });

  test("macOS hides the title bar and places the traffic lights", () => {
    expect(titleBarOptions("darwin")).toEqual({
      titleBarStyle: "hidden",
      trafficLightPosition: { x: 16, y: 16 },
    });
  });

  test.each(["linux", "freebsd", "openbsd"])("%s never gets a frameless or overlay window", (platform) => {
    const options = titleBarOptions(platform);
    expect(options).not.toHaveProperty("titleBarStyle");
    expect(options).not.toHaveProperty("titleBarOverlay");
    expect(options).not.toHaveProperty("frame");
  });

  test("each call returns a fresh overlay object", () => {
    titleBarOptions("win32").titleBarOverlay.color = "#000000";
    expect(titleBarOptions("win32").titleBarOverlay.color).toBe("#1A1917");
  });
});

describe("titleBarKind", () => {
  test.each([
    ["darwin", "mac"],
    ["win32", "overlay"],
    ["linux", "native"],
  ])("%s -> %s", (platform, kind) => {
    expect(titleBarKind(platform)).toBe(kind);
  });
});

describe("the renderer learns the same frame from preload.cjs", () => {
  const realPlatform = Object.getOwnPropertyDescriptor(process, "platform");

  afterEach(() => {
    Object.defineProperty(process, "platform", realPlatform);
    jest.resetModules();
  });

  function exposedTitleBar(platform) {
    Object.defineProperty(process, "platform", { ...realPlatform, value: platform });
    const exposed = {};
    jest.isolateModules(() => {
      jest.doMock("electron", () => ({
        contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
        ipcRenderer: { invoke: jest.fn(), on: jest.fn(), removeListener: jest.fn(), send: jest.fn() },
      }));
      require(path.join(WEBUI, "preload.cjs"));
    });
    return exposed.gaiaWindow.titleBar;
  }

  test.each(["darwin", "win32", "linux", "freebsd"])("%s", (platform) => {
    expect(exposedTitleBar(platform)).toBe(titleBarKind(platform));
  });
});

describe("main.cjs uses the shared frame options", () => {
  const MAIN = fs.readFileSync(path.join(WEBUI, "main.cjs"), "utf8");

  test("the main window spreads titleBarOptions for this platform", () => {
    expect(MAIN).toContain("...titleBarOptions(process.platform),");
  });

  test("only the overlay window takes title-bar colors", () => {
    expect(MAIN).toMatch(/TITLE_BAR_KIND === "overlay" && mainWindow[^\n]*\n\s*mainWindow\.setTitleBarOverlay\(/);
  });
});

describe("the title-bar height matches the renderer's --titlebar-h", () => {
  test("index.css", () => {
    const css = fs.readFileSync(path.join(WEBUI, "src", "styles", "index.css"), "utf8");
    expect(css).toContain(`--titlebar-h: ${TITLE_BAR_HEIGHT}px;`);
  });
});
