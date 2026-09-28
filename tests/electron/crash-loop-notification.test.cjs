// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * An agent crash loop, end to end in the main process: the REAL
 * AgentProcessManager restarts a crashing agent until it hits the crash
 * limit, the REAL NotificationService turns that into a notification, and
 * the count lands on the REAL TrayManager. Before the fix, the crash-limit
 * notification called a TrayManager method that did not exist, the throw
 * escaped the child's `exit` handler, and the app exited.
 */

const { EventEmitter } = require("events");

const mockChildren = [];
function mockMakeChild() {
  const child = new EventEmitter();
  child.stdin = { write: jest.fn(), destroyed: false };
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.pid = 40000 + mockChildren.length;
  child.exitCode = null;
  child.kill = jest.fn();
  mockChildren.push(child);
  return child;
}
jest.mock("child_process", () => ({ spawn: jest.fn(() => mockMakeChild()) }));

const mockManifest = JSON.stringify({
  manifest_version: 1,
  agents: [
    {
      id: "crashy",
      name: "Crashy",
      binaries: { win32: "crashy.exe", darwin: "crashy", linux: "crashy" },
    },
  ],
});
const mockConfig = JSON.stringify({
  agents: { crashy: { autoStart: false, restartOnCrash: true } },
  tray: { minimizeToTray: false },
});
jest.mock("fs", () => ({
  existsSync: jest.fn((p) => {
    const s = String(p);
    return /agent-manifest\.json|tray-config\.json|crashy|tray-icon/.test(s);
  }),
  readFileSync: jest.fn((p) => {
    const s = String(p);
    if (s.includes("agent-manifest.json")) return mockManifest;
    if (s.includes("tray-config.json")) return mockConfig;
    return "[]";
  }),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));

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

const { spawn } = require("child_process");
const AgentProcessManager = require("../../src/gaia/apps/webui/services/agent-process-manager.cjs");
const TrayManager = require("../../src/gaia/apps/webui/services/tray-manager.cjs");
const NotificationService = require("../../src/gaia/apps/webui/services/notification-service.cjs");

async function flushMicrotasks() {
  for (let i = 0; i < 5; i++) await Promise.resolve();
}

describe("agent crash loop -> notification -> tray", () => {
  let manager;
  let tray;
  let service;
  let logSpy;

  beforeEach(() => {
    jest.useFakeTimers();
    mockChildren.length = 0;
    spawn.mockClear();
    electronMock.ipcMain._handlers.clear();
    logSpy = jest.spyOn(console, "log").mockImplementation(() => {});
    jest.spyOn(console, "warn").mockImplementation(() => {});

    const win = new electronMock.BrowserWindow();
    win.isMinimized = jest.fn(() => false);
    win.restore = jest.fn();
    manager = new AgentProcessManager(win);
    tray = new TrayManager(win);
    tray.create();
    service = new NotificationService(win, manager, tray);
  });

  afterEach(() => {
    service.destroy();
    jest.clearAllTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  test("the crash-limit notification does not throw, and its count reaches the tray", async () => {
    const crashLimit = jest.fn();
    manager.on("agent-crash-limit", crashLimit);

    await manager.startAgent("crashy");

    // Three crashes are restarted (2 s apart); the fourth trips the limit.
    for (let crash = 1; crash <= 4; crash++) {
      const child = mockChildren[mockChildren.length - 1];
      expect(() => child.emit("exit", 1, null)).not.toThrow();
      if (crash < 4) {
        jest.advanceTimersByTime(2001);
        await flushMicrotasks();
      }
    }

    expect(spawn).toHaveBeenCalledTimes(4); // first start + three restarts
    expect(crashLimit).toHaveBeenCalledWith("crashy", 4);

    const limitNotice = service.notifications.find(
      (n) => n.title === "Agent Crash Limit Reached"
    );
    expect(limitNotice).toBeDefined();
    expect(limitNotice.message).toContain("crashy");

    expect(tray.notificationCount).toBe(1);
    expect(tray.tray._toolTip).toBe("GAIA — 1 unread notification");
    expect(logSpy.mock.calls.map((c) => c.join(" "))).toContain("[tray] Unread notifications: 1");
  });
});
