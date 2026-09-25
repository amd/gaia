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

describe("TrayManager.setNotificationCount", () => {
  test("reflects the unread count in the tray tooltip", () => {
    setPlatform("win32");
    const mgr = new TrayManager(createMockWindow());
    mgr.create();

    mgr.setNotificationCount(1);
    expect(mgr.tray._toolTip).toBe("GAIA — 1 unread notification");
    mgr.setNotificationCount(4);
    expect(mgr.tray._toolTip).toBe("GAIA — 4 unread notifications");
    mgr.setNotificationCount(0);
    expect(mgr.tray._toolTip).toBe("GAIA");
  });

  test("does not call app.setBadgeCount on Windows", () => {
    setPlatform("win32");
    const mgr = new TrayManager(createMockWindow());
    mgr.setNotificationCount(3);
    expect(electronMock.app.setBadgeCount).not.toHaveBeenCalled();
  });

  test("sets the dock badge on macOS", () => {
    setPlatform("darwin");
    const mgr = new TrayManager(createMockWindow());
    mgr.setNotificationCount(2);
    expect(electronMock.app.setBadgeCount).toHaveBeenCalledWith(2);
  });

  test("a count set before create() is applied when the tray appears", () => {
    setPlatform("win32");
    const mgr = new TrayManager(createMockWindow());
    mgr.setNotificationCount(5);
    mgr.create();
    expect(mgr.tray._toolTip).toBe("GAIA — 5 unread notifications");
  });

  test.each([[-1], [NaN], [Infinity], [undefined], ["3"]])(
    "treats %p as zero",
    (bad) => {
      setPlatform("win32");
      const mgr = new TrayManager(createMockWindow());
      mgr.create();
      mgr.setNotificationCount(bad);
      expect(mgr.notificationCount).toBe(0);
      expect(mgr.tray._toolTip).toBe("GAIA");
    }
  );

  test("is a no-op on the tooltip after the tray is destroyed", () => {
    setPlatform("win32");
    const mgr = new TrayManager(createMockWindow());
    mgr.create();
    const destroyed = mgr.tray;
    destroyed._destroyed = true;
    destroyed.setToolTip.mockClear();
    mgr.setNotificationCount(2);
    expect(destroyed.setToolTip).not.toHaveBeenCalled();
    expect(mgr.notificationCount).toBe(2);
  });
});

describe("tray:set-config validation", () => {
  function setConfig(payload) {
    setPlatform("win32");
    new TrayManager(createMockWindow());
    return electronMock.ipcMain.simulateInvoke("tray:set-config", payload);
  }

  beforeEach(() => {
    electronMock.app.setLoginItemSettings = jest.fn();
  });

  test.each([[null], [undefined], ["tray"], [[]]])(
    "rejects a %p payload with a clear error",
    async (bad) => {
      await expect(setConfig(bad)).rejects.toThrow(/tray:set-config expects an object/);
      expect(fs.writeFileSync).not.toHaveBeenCalled();
    }
  );

  test("persists only the known tray keys", async () => {
    const result = await setConfig({
      tray: { minimizeToTray: false, injected: "x" },
    });
    expect(result.tray).toEqual({
      minimizeToTray: false,
      startMinimized: false,
      startOnLogin: false,
    });
    const saved = JSON.parse(fs.writeFileSync.mock.calls[0][1]);
    expect(Object.keys(saved.tray).sort()).toEqual([
      "minimizeToTray",
      "startMinimized",
      "startOnLogin",
    ]);
  });

  test.each([["true"], [1], [null]])(
    "rejects a non-boolean startOnLogin (%p) without touching the login item",
    async (bad) => {
      await expect(setConfig({ tray: { startOnLogin: bad } })).rejects.toThrow(
        /tray\.startOnLogin must be a boolean/
      );
      expect(electronMock.app.setLoginItemSettings).not.toHaveBeenCalled();
      expect(fs.writeFileSync).not.toHaveBeenCalled();
    }
  );

  test("applies a boolean startOnLogin to the OS login item", async () => {
    await setConfig({ tray: { startOnLogin: true } });
    expect(electronMock.app.setLoginItemSettings).toHaveBeenCalledWith(
      expect.objectContaining({ openAtLogin: true })
    );
  });

  test("rejects a non-object tray section", async () => {
    await expect(setConfig({ tray: "on" })).rejects.toThrow(/tray must be an object/);
    expect(fs.writeFileSync).not.toHaveBeenCalled();
  });
});
