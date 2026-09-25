// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * NotificationService wired to the REAL TrayManager (not a stub that happens
 * to have setNotificationCount). The existing unit suite mocked the tray with
 * the method the real class lacked, so the missing-method crash never showed.
 */

const { EventEmitter } = require("events");
const electronMock = require("electron");

class MockNotification extends EventEmitter {
  constructor(opts = {}) {
    super();
    this.opts = opts;
  }
  show() {}
}
MockNotification.isSupported = jest.fn(() => true);
electronMock.Notification = MockNotification;

// Only the tray-icon assets "exist" — no persisted notifications or tray config.
jest.mock("fs", () => ({
  existsSync: jest.fn((p) => /tray-icon/.test(String(p))),
  readFileSync: jest.fn(() => "[]"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));

const TrayManager = require("../../src/gaia/apps/webui/services/tray-manager.cjs");
const NotificationService = require("../../src/gaia/apps/webui/services/notification-service.cjs");

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

describe("NotificationService + real TrayManager", () => {
  let tray;
  let apm;
  let service;

  beforeEach(() => {
    jest.clearAllMocks();
    setPlatform("win32");
    electronMock.ipcMain._handlers.clear();
    const win = createMockWindow();
    tray = new TrayManager(win);
    tray.create();
    apm = new EventEmitter();
    apm._sendJsonRpcRaw = jest.fn();
    service = new NotificationService(win, apm, tray);
  });

  afterEach(() => {
    service.destroy();
    setPlatform(ORIGINAL_PLATFORM);
  });

  test("the real TrayManager implements the method NotificationService calls", () => {
    expect(typeof tray.setNotificationCount).toBe("function");
  });

  test("an agent crash-limit event updates the tray tooltip instead of throwing", () => {
    expect(() => apm.emit("agent-crash-limit", "crashy", 6)).not.toThrow();
    expect(tray.notificationCount).toBe(1);
    expect(tray.tray._toolTip).toBe("GAIA — 1 unread notification");
  });

  test("a crash loop accumulates the count, and markAllRead clears it", () => {
    apm.emit("status-change", { agentId: "a", status: "stopped", detail: "exit 1" });
    apm.emit("status-change", { agentId: "a", status: "stopped", detail: "exit 1" });
    apm.emit("agent-crash-limit", "a", 6);
    expect(tray.notificationCount).toBe(3);
    expect(tray.tray._toolTip).toBe("GAIA — 3 unread notifications");

    service.markAllRead();
    expect(tray.notificationCount).toBe(0);
    expect(tray.tray._toolTip).toBe("GAIA");
  });
});

describe("NotificationService listener containment", () => {
  let apm;
  let service;
  let errorSpy;

  beforeEach(() => {
    jest.clearAllMocks();
    electronMock.ipcMain._handlers.clear();
    apm = new EventEmitter();
    apm._sendJsonRpcRaw = jest.fn();
    const brokenTray = {
      setNotificationCount: jest.fn(() => {
        throw new TypeError("tray exploded");
      }),
    };
    service = new NotificationService(createMockWindow(), apm, brokenTray);
    errorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    service.destroy();
    errorSpy.mockRestore();
  });

  test.each([
    ["agent-crash-limit", ["crashy", 6]],
    ["status-change", [{ agentId: "a", status: "stopped", detail: "boom" }]],
    ["agent-notification", ["a", { type: "info", message: "hi" }]],
  ])("a throw inside the %s listener is logged, not escaped", (event, args) => {
    // Unguarded, EventEmitter.emit rethrows synchronously — from a child
    // `exit` handler that is an uncaughtException and the app exits.
    expect(() => apm.emit(event, ...args)).not.toThrow();
    const logged = errorSpy.mock.calls.map((c) => c.join(" ")).join("\n");
    expect(logged).toContain(`Listener for "${event}" threw`);
    expect(logged).toContain("tray exploded");
  });
});
