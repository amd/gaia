// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for TrayManager icon loading
 * (src/gaia/apps/webui/services/tray-manager.cjs)
 *
 * Covers: per-platform tray-icon selection (small purpose-built assets, never
 * the 4K app icon), macOS template-image flag, and the fail-loud behaviour
 * when a tray-icon asset is missing.
 */

const electronMock = require("electron");

// Mock fs so the manager never touches the real filesystem. Tests toggle
// existsSync per case to exercise the missing-asset path.
jest.mock("fs", () => ({
  existsSync: jest.fn(() => true),
  readFileSync: jest.fn(() => "{}"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));
const fs = require("fs");

const TrayManager = require("../../src/gaia/apps/webui/services/tray-manager.cjs");

// ── Helpers ──────────────────────────────────────────────────────────────

const ORIGINAL_PLATFORM = process.platform;

function setPlatform(platform) {
  Object.defineProperty(process, "platform", { value: platform });
}

function createMockWindow() {
  const win = new electronMock.BrowserWindow();
  win.isMinimized = jest.fn(() => false);
  win.restore = jest.fn();
  return win;
}

beforeEach(() => {
  jest.clearAllMocks();
  fs.existsSync.mockReturnValue(true);
});

afterEach(() => {
  setPlatform(ORIGINAL_PLATFORM);
});

// ── Tests ────────────────────────────────────────────────────────────────

describe("TrayManager icon loading", () => {
  test("macOS loads the template asset and marks it a template image", () => {
    setPlatform("darwin");
    const mgr = new TrayManager(createMockWindow());

    expect(electronMock.nativeImage.createFromPath).toHaveBeenCalledTimes(1);
    const loadedPath = electronMock.nativeImage.createFromPath.mock.calls[0][0];
    expect(loadedPath).toContain("tray-iconTemplate.png");
    expect(mgr._icon._isTemplate).toBe(true);
  });

  test("Windows loads the .ico tray asset", () => {
    setPlatform("win32");
    new TrayManager(createMockWindow());

    const loadedPath = electronMock.nativeImage.createFromPath.mock.calls[0][0];
    expect(loadedPath).toContain("tray-icon.ico");
  });

  test("Linux loads the full-colour PNG tray asset", () => {
    setPlatform("linux");
    new TrayManager(createMockWindow());

    const loadedPath = electronMock.nativeImage.createFromPath.mock.calls[0][0];
    expect(loadedPath).toContain("tray-icon.png");
    expect(loadedPath).not.toContain("Template");
  });

  test("never feeds the 4K app icon (icon.png) into the tray", () => {
    setPlatform("darwin");
    new TrayManager(createMockWindow());

    const loadedPath = electronMock.nativeImage.createFromPath.mock.calls[0][0];
    expect(loadedPath).not.toMatch(/[/\\]icon\.png$/);
  });

  test("fails loudly when the tray-icon asset is missing", () => {
    setPlatform("darwin");
    fs.existsSync.mockReturnValue(false);

    expect(() => new TrayManager(createMockWindow())).toThrow(
      /icon asset missing/
    );
    expect(electronMock.nativeImage.createEmpty).not.toHaveBeenCalled();
  });
});
