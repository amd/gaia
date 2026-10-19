// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Chat alerts and keep-awake in the desktop app (#5226): the OS banner, the
 * taskbar flash, the tray badge for chats that need you, and the sleep block.
 */

const { EventEmitter } = require("events");
const fs = require("fs");
const path = require("path");

const electronMock = require("electron");

class MockNotification extends EventEmitter {
  constructor(opts = {}) {
    super();
    this.opts = opts;
    MockNotification.instances.push(this);
  }
  show() {}
}
MockNotification.instances = [];
MockNotification.isSupported = jest.fn(() => true);
electronMock.Notification = MockNotification;

let nextBlockerId = 1;
const started = new Set();
electronMock.powerSaveBlocker = {
  start: jest.fn(() => {
    const id = nextBlockerId++;
    started.add(id);
    return id;
  }),
  stop: jest.fn((id) => started.delete(id)),
  isStarted: jest.fn((id) => started.has(id)),
};

// The tray icon must "exist"; the notification log reads as empty and is never written.
jest.mock("fs", () => {
  const real = jest.requireActual("fs");
  return {
    ...real,
    existsSync: jest.fn(() => true),
    readFileSync: jest.fn((p, enc) =>
      String(p).endsWith("notifications.json") ? "[]" : real.readFileSync(p, enc)
    ),
    writeFileSync: jest.fn(),
    mkdirSync: jest.fn(),
  };
});

const WEBUI = path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui");
const NotificationService = require(path.join(WEBUI, "services", "notification-service.cjs"));
const KeepAwake = require(path.join(WEBUI, "services", "keep-awake.cjs"));
const TrayManager = require(path.join(WEBUI, "services", "tray-manager.cjs"));

function createMockWindow({ focused = false } = {}) {
  const win = new electronMock.BrowserWindow();
  win.isMinimized = jest.fn(() => false);
  win.restore = jest.fn();
  win.show = jest.fn();
  win.focus = jest.fn();
  win.isFocused = jest.fn(() => focused);
  win.isDestroyed = jest.fn(() => false);
  win.flashFrame = jest.fn();
  return win;
}

beforeEach(() => {
  jest.clearAllMocks();
  MockNotification.instances = [];
  electronMock.ipcMain._handlers.clear();
});

describe("chat banners", () => {
  test("shows a silent OS banner that opens its chat when clicked", async () => {
    const win = createMockWindow();
    new NotificationService(win, null, null);
    const shown = await electronMock.ipcMain.simulateInvoke("notification:show", {
      title: "GAIA needs your approval",
      body: "Fix the build",
      sessionId: "abc",
    });
    expect(shown).toBe(true);
    const banner = MockNotification.instances[0];
    expect(banner.opts).toMatchObject({ title: "GAIA needs your approval", body: "Fix the build", silent: true });

    banner.emit("click");
    expect(win.focus).toHaveBeenCalled();
    expect(win.webContents.send).toHaveBeenCalledWith("tray:navigate", "session:abc");
  });

  test("refuses a malformed payload and reports no OS support", async () => {
    new NotificationService(createMockWindow(), null, null);
    await expect(electronMock.ipcMain.simulateInvoke("notification:show", { body: "x" })).rejects.toThrow(TypeError);
    MockNotification.isSupported.mockReturnValueOnce(false);
    jest.spyOn(console, "warn").mockImplementation(() => {});
    await expect(
      electronMock.ipcMain.simulateInvoke("notification:show", { title: "t", body: "", sessionId: "a" })
    ).resolves.toBe(false);
  });
});

describe("taskbar flash", () => {
  test("flashes until focused, only when the window is not focused", async () => {
    const win = createMockWindow({ focused: false });
    new NotificationService(win, null, null);
    expect(await electronMock.ipcMain.simulateInvoke("notification:flash")).toBe(true);
    expect(win.flashFrame).toHaveBeenCalledWith(true);
    win.emit("focus");
    expect(win.flashFrame).toHaveBeenLastCalledWith(false);

    const focusedWin = createMockWindow({ focused: true });
    electronMock.ipcMain._handlers.clear();
    new NotificationService(focusedWin, null, null);
    expect(await electronMock.ipcMain.simulateInvoke("notification:flash")).toBe(false);
    expect(focusedWin.flashFrame).not.toHaveBeenCalled();
  });
});

describe("tray badge for chats that need you", () => {
  const newTray = () => new TrayManager(createMockWindow(), { reopenWindow: jest.fn() });
  const idle = {
    backendConnected: true,
    modelServerRunning: true,
    model: null,
    waitingOnModel: null,
    running: [],
    needsYou: [],
    recent: [],
  };

  test("counts chats beside unread notifications in the tooltip", async () => {
    const tray = newTray();
    tray.create();
    tray.setActivity(idle);
    new NotificationService(createMockWindow(), null, tray);
    await electronMock.ipcMain.simulateInvoke("notification:attention", 2);
    expect(tray.tray.setToolTip).toHaveBeenLastCalledWith("GAIA — idle · 2 chats need you");
    tray.setNotificationCount(1);
    expect(tray.tray.setToolTip).toHaveBeenLastCalledWith(
      "GAIA — idle · 2 chats need you · 1 unread notification"
    );
    await electronMock.ipcMain.simulateInvoke("notification:attention", 0);
    expect(tray.tray.setToolTip).toHaveBeenLastCalledWith("GAIA — idle · 1 unread notification");
  });

  test("a needs-you state already names the chat, so the count is not repeated", async () => {
    const tray = newTray();
    tray.create();
    tray.setActivity({ ...idle, needsYou: [{ id: "a", title: "Trip plan", reason: "approval" }] });
    new NotificationService(createMockWindow(), null, tray);
    await electronMock.ipcMain.simulateInvoke("notification:attention", 1);
    expect(tray.tray.setToolTip).toHaveBeenLastCalledWith("GAIA — 'Trip plan' needs your approval");
  });

  test("rejects a bad count, and fails loudly with no tray", async () => {
    const tray = newTray();
    expect(() => tray.setAttentionCount(-1)).toThrow(TypeError);
    expect(() => tray.setAttentionCount(1.5)).toThrow(TypeError);
    new NotificationService(createMockWindow(), null, null);
    await expect(electronMock.ipcMain.simulateInvoke("notification:attention", 1)).rejects.toThrow(/tray/);
  });
});

describe("keep-awake", () => {
  test("holds one sleep block while on, and releases it", async () => {
    const ka = new KeepAwake();
    expect(await electronMock.ipcMain.simulateInvoke("power:keep-awake", true)).toBe(true);
    expect(await electronMock.ipcMain.simulateInvoke("power:keep-awake", true)).toBe(true);
    expect(electronMock.powerSaveBlocker.start).toHaveBeenCalledTimes(1);
    expect(electronMock.powerSaveBlocker.start).toHaveBeenCalledWith("prevent-app-suspension");
    expect(await electronMock.ipcMain.simulateInvoke("power:keep-awake", false)).toBe(false);
    expect(electronMock.powerSaveBlocker.stop).toHaveBeenCalledTimes(1);
    expect(ka.active).toBe(false);
  });

  test("rejects a non-boolean, and destroy releases the block and the channel", async () => {
    const ka = new KeepAwake();
    await expect(electronMock.ipcMain.simulateInvoke("power:keep-awake", "yes")).rejects.toThrow(TypeError);
    ka.set(true);
    ka.destroy();
    expect(ka.active).toBe(false);
    await expect(electronMock.ipcMain.simulateInvoke("power:keep-awake", true)).rejects.toThrow(/No handler/);
  });
});

describe("preload ↔ main channel contract", () => {
  test("every new channel the preload invokes is registered by a service", () => {
    const preload = fs.readFileSync(path.join(WEBUI, "preload.cjs"), "utf8");
    new NotificationService(
      createMockWindow(),
      null,
      new TrayManager(createMockWindow(), { reopenWindow: jest.fn() })
    );
    new KeepAwake();
    for (const channel of ["notification:show", "notification:flash", "notification:attention", "power:keep-awake"]) {
      expect(preload).toContain(`"${channel}"`);
      expect(electronMock.ipcMain._handlers.has(channel)).toBe(true);
    }
  });
});
