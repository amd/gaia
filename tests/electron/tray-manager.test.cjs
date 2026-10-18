// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for TrayManager (src/gaia/apps/webui/services/tray-manager.cjs)
 *
 * Covers: per-platform tray-icon selection (small purpose-built assets, never
 * the 4K app icon) and the fail-loud behaviour when one is missing; the live
 * state on the icon, tooltip and menu; the Windows taskbar overlay; the menu's
 * actions, including Free memory; the one-time close notice; and tray config.
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

/** A window snapshot with nothing going on; override what a test needs. */
function activity(overrides = {}) {
  return {
    backendConnected: true,
    modelServerRunning: true,
    model: { name: "Gemma-4-E4B-it-GGUF", where: "gpu" },
    waitingOnModel: null,
    running: [],
    needsYou: [],
    recent: [],
    ...overrides,
  };
}

/** OS login items that read back what was last set, as Windows and macOS do. */
function mockLoginItems() {
  let openAtLogin = false;
  electronMock.app.setLoginItemSettings = jest.fn((s) => {
    openAtLogin = s.openAtLogin;
  });
  electronMock.app.getLoginItemSettings = jest.fn(() => ({ openAtLogin }));
}

/** A TrayManager whose reopenWindow hands back a fresh mock window. */
function newTray(win, options = {}) {
  return new TrayManager(win, { reopenWindow: jest.fn(() => createMockWindow()), ...options });
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
  const loadedFiles = () =>
    electronMock.nativeImage.createFromPath.mock.calls.map((c) => require("path").basename(c[0]));

  test("macOS draws on the template assets and marks the image a template", () => {
    setPlatform("darwin");
    const mgr = newTray(createMockWindow());

    expect(loadedFiles()).toEqual(["tray-iconTemplate.png", "tray-iconTemplate@2x.png"]);
    expect(mgr._icon._isTemplate).toBe(true);
    expect(mgr._icon._reps.map((r) => [r.scaleFactor, r.width])).toEqual([[1, 16], [2, 32]]);
  });

  test("Windows draws a 16 DIP icon at every common display scale", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());

    expect(new Set(loadedFiles())).toEqual(new Set(["tray-icon.png"]));
    expect(mgr._icon._reps.map((r) => [r.scaleFactor, r.width])).toEqual([
      [1, 16],
      [1.25, 20],
      [1.5, 24],
      [2, 32],
    ]);
    expect(mgr._icon._isTemplate).toBe(false);
  });

  test("Linux draws on the full-colour PNG assets", () => {
    setPlatform("linux");
    const mgr = newTray(createMockWindow());

    expect(loadedFiles()).toEqual(["tray-icon.png", "tray-icon@2x.png"]);
    expect(mgr._icon._reps.map((r) => r.width)).toEqual([32, 64]);
  });

  test("never feeds the 4K app icon (icon.png) into the tray", () => {
    for (const platform of ["darwin", "win32", "linux"]) {
      setPlatform(platform);
      electronMock.nativeImage.createFromPath.mockClear();
      const mgr = newTray(createMockWindow());
      mgr.create();
      mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
      expect(loadedFiles()).not.toContain("icon.png");
    }
  });

  test("each representation carries a full bitmap for its pixel size", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    for (const rep of mgr._icon._reps) {
      expect(rep.buffer.length).toBe(rep.width * rep.height * 4);
    }
  });

  test("fails loudly when the tray-icon asset is missing", () => {
    setPlatform("darwin");
    fs.existsSync.mockReturnValue(false);

    expect(() => newTray(createMockWindow())).toThrow(
      /icon asset missing/
    );
  });

  test("fails loudly when the tray-icon asset cannot be decoded", () => {
    setPlatform("linux");
    electronMock.nativeImage.createFromPath.mockImplementationOnce((p) => ({
      _path: p,
      isEmpty: () => true,
    }));

    expect(() => newTray(createMockWindow())).toThrow(
      /icon asset could not be decoded: .*tray-icon\.png/
    );
  });
});

// The Linux tray shipped a flat red 16px square as its "logo" (#5268).
describe("Linux tray-icon asset", () => {
  const realFs = jest.requireActual("fs");
  const zlib = require("zlib");
  const path = require("path");
  const { assertTrayLogoPng } = require("./_helpers/tray-icon-png.cjs");

  const WEBUI = path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui");
  const ASSETS = path.join(WEBUI, "assets");

  function solidPng(size, rgba) {
    const chunk = (type, data) => {
      const len = Buffer.alloc(4);
      len.writeUInt32BE(data.length);
      // The decoder under test skips CRCs, so a zero CRC is fine here.
      return Buffer.concat([len, Buffer.from(type, "ascii"), data, Buffer.alloc(4)]);
    };
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(size, 0);
    ihdr.writeUInt32BE(size, 4);
    ihdr[8] = 8;
    ihdr[9] = 6;
    const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(size * 4).fill(Buffer.from(rgba))]);
    return Buffer.concat([
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      chunk("IHDR", ihdr),
      chunk("IDAT", zlib.deflateSync(Buffer.concat(Array(size).fill(row)))),
      chunk("IEND", Buffer.alloc(0)),
    ]);
  }

  test.each([
    ["tray-icon.png", 32],
    ["tray-icon@2x.png", 64],
  ])("%s is the %ipx GAIA logo, not a placeholder", (file, size) => {
    const buf = realFs.readFileSync(path.join(ASSETS, file));
    expect(() => assertTrayLogoPng(buf, size, file)).not.toThrow();
  });

  test("the check rejects the old flat red square", () => {
    expect(() => assertTrayLogoPng(solidPng(32, [226, 60, 64, 255]), 32, "red")).toThrow(
      /corner .* is opaque/
    );
  });

  test("the check rejects a transparent-cornered flat placeholder", () => {
    const buf = solidPng(32, [226, 60, 64, 0]);
    expect(() => assertTrayLogoPng(buf, 32, "flat")).toThrow(/distinct visible colours/);
  });

  test("the check rejects a wrong-sized icon", () => {
    const buf = realFs.readFileSync(path.join(ASSETS, "tray-icon.png"));
    expect(() => assertTrayLogoPng(buf, 16, "small")).toThrow(/expected 16x16, got 32x32/);
  });

  test("Linux resolves the icon next to services/, which is packed into the asar", () => {
    setPlatform("linux");
    newTray(createMockWindow());
    const loadedPath = electronMock.nativeImage.createFromPath.mock.calls[0][0];
    expect(path.normalize(loadedPath)).toBe(path.join(ASSETS, "tray-icon.png"));

    // Packaged, the same __dirname-relative path lives inside app.asar, so
    // both directories must stay in electron-builder's `files:` list.
    const builderYml = realFs.readFileSync(path.join(WEBUI, "electron-builder.yml"), "utf8");
    expect(builderYml).toMatch(/^\s*-\s*services\/\*\*\/\*\s*$/m);
    expect(builderYml).toMatch(/^\s*-\s*assets\/\*\*\/\*\s*$/m);
  });
});

describe("TrayManager.setNotificationCount", () => {
  test("reflects the unread count in the tray tooltip", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    mgr.create();

    mgr.setNotificationCount(1);
    expect(mgr.tray._toolTip).toBe("GAIA — starting · 1 unread notification");
    mgr.setNotificationCount(4);
    expect(mgr.tray._toolTip).toBe("GAIA — starting · 4 unread notifications");
    mgr.setNotificationCount(0);
    expect(mgr.tray._toolTip).toBe("GAIA — starting");
  });

  test("does not call app.setBadgeCount on Windows", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    mgr.setNotificationCount(3);
    expect(electronMock.app.setBadgeCount).not.toHaveBeenCalled();
  });

  test("sets the dock badge on macOS", () => {
    setPlatform("darwin");
    const mgr = newTray(createMockWindow());
    mgr.setNotificationCount(2);
    expect(electronMock.app.setBadgeCount).toHaveBeenCalledWith(2);
  });

  test("a count set before create() is applied when the tray appears", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    mgr.setNotificationCount(5);
    mgr.create();
    expect(mgr.tray._toolTip).toBe("GAIA — starting · 5 unread notifications");
  });

  test.each([[-1], [NaN], [Infinity], [undefined], ["3"]])(
    "treats %p as zero",
    (bad) => {
      setPlatform("win32");
      const mgr = newTray(createMockWindow());
      mgr.create();
      mgr.setNotificationCount(bad);
      expect(mgr.notificationCount).toBe(0);
      expect(mgr.tray._toolTip).toBe("GAIA — starting");
    }
  );

  test("is a no-op on the tooltip after the tray is destroyed", () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
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
    newTray(createMockWindow());
    return electronMock.ipcMain.simulateInvoke("tray:set-config", payload);
  }

  beforeEach(() => {
    mockLoginItems();
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

describe("agent config persistence", () => {
  const AGENT_CFG = { autoStart: true, restartOnCrash: false, logLevel: "debug" };
  let disk;

  beforeEach(() => {
    disk = null;
    fs.existsSync.mockImplementation(
      (p) => !String(p).endsWith("tray-config.json") || disk !== null
    );
    fs.readFileSync.mockImplementation(() => disk);
    fs.writeFileSync.mockImplementation((_p, data) => {
      disk = data;
    });
  });

  afterEach(() => {
    fs.existsSync.mockImplementation(() => true);
    fs.readFileSync.mockImplementation(() => "{}");
    fs.writeFileSync.mockImplementation(() => {});
  });

  function freshManager() {
    setPlatform("win32");
    return newTray(createMockWindow());
  }

  const invoke = (channel, ...args) =>
    electronMock.ipcMain.simulateInvoke(channel, ...args);

  test("a saved agent config survives a reload from disk", async () => {
    freshManager();
    await invoke("tray:set-config", { agents: { "email-agent": AGENT_CFG } });

    freshManager();
    const reloaded = await invoke("tray:get-config");
    expect(reloaded.agents).toEqual({ "email-agent": AGENT_CFG });
  });

  test("saving one agent keeps the others and the tray settings", async () => {
    freshManager();
    await invoke("tray:set-config", {
      tray: { minimizeToTray: false },
      agents: { a: AGENT_CFG },
    });
    const other = { autoStart: false, restartOnCrash: true, logLevel: "warn" };
    await invoke("tray:set-config", { agents: { b: other } });

    freshManager();
    const reloaded = await invoke("tray:get-config");
    expect(reloaded.agents).toEqual({ a: AGENT_CFG, b: other });
    expect(reloaded.tray.minimizeToTray).toBe(false);
  });

  test("an unconfigured install reports an empty agents section", async () => {
    freshManager();
    const cfg = await invoke("tray:get-config");
    expect(cfg.agents).toEqual({});
  });

  test("a malformed agents section on disk keeps the tray settings", async () => {
    disk = JSON.stringify({
      tray: { minimizeToTray: false, startMinimized: true },
      agents: { "email-agent": { autoStart: "yes" } },
    });
    const errors = jest.spyOn(console, "error").mockImplementation(() => {});

    freshManager();
    const cfg = await invoke("tray:get-config");

    expect(cfg.tray.minimizeToTray).toBe(false);
    expect(cfg.tray.startMinimized).toBe(true);
    expect(cfg.agents).toEqual({});
    expect(errors).toHaveBeenCalledWith(
      expect.stringMatching(/Ignoring invalid agents section/)
    );
    errors.mockRestore();
  });

  test("a non-object tray section falls back without losing agents", async () => {
    disk = JSON.stringify({ tray: "on", agents: { a: AGENT_CFG } });

    freshManager();
    const cfg = await invoke("tray:get-config");

    expect(cfg.tray).toEqual({
      minimizeToTray: true,
      startMinimized: false,
      startOnLogin: false,
    });
    expect(cfg.agents).toEqual({ a: AGENT_CFG });
  });

  test("a failed write rejects and leaves the in-memory config unchanged", async () => {
    freshManager();
    fs.writeFileSync.mockImplementation(() => {
      throw new Error("EACCES: permission denied");
    });
    await expect(
      invoke("tray:set-config", { agents: { a: AGENT_CFG } })
    ).rejects.toThrow(/Could not save settings to .*EACCES/);
    const cfg = await invoke("tray:get-config");
    expect(cfg.agents).toEqual({});
  });

  test("drops unknown per-agent keys", async () => {
    freshManager();
    const result = await invoke("tray:set-config", {
      agents: { a: { ...AGENT_CFG, injected: "x" } },
    });
    expect(result.agents.a).toEqual(AGENT_CFG);
  });

  test.each([
    [{ agents: "on" }, /agents must be an object/],
    [{ agents: { a: null } }, /agents\.a must be an object/],
    [
      { agents: { a: { ...AGENT_CFG, autoStart: "yes" } } },
      /agents\.a\.autoStart must be a boolean/,
    ],
    [
      { agents: { a: { autoStart: true, logLevel: "info" } } },
      /agents\.a\.restartOnCrash must be a boolean/,
    ],
    [
      { agents: { a: { ...AGENT_CFG, logLevel: "loud" } } },
      /agents\.a\.logLevel must be one of/,
    ],
    [{ agents: { "": AGENT_CFG } }, /agent id must be a non-empty string/],
  ])("rejects invalid agent config %p without writing", async (payload, err) => {
    freshManager();
    await expect(invoke("tray:set-config", payload)).rejects.toThrow(err);
    expect(disk).toBeNull();
  });
});

describe("tray config already on disk from the unvalidated handler", () => {
  const DEFAULT_TRAY = {
    minimizeToTray: true,
    startMinimized: false,
    startOnLogin: false,
  };

  function managerWithStoredConfig(stored) {
    setPlatform("win32");
    fs.readFileSync.mockReturnValue(
      typeof stored === "string" ? stored : JSON.stringify(stored)
    );
    return newTray(createMockWindow());
  }

  beforeEach(() => {
    mockLoginItems();
  });

  afterEach(() => {
    fs.readFileSync.mockReturnValue("{}");
  });

  test("a non-boolean on disk is replaced by the default at load", () => {
    const mgr = managerWithStoredConfig({
      tray: { minimizeToTray: true, startMinimized: false, startOnLogin: "true" },
    });
    expect(mgr.config.tray).toEqual(DEFAULT_TRAY);
  });

  test("a non-boolean on disk does not block saving an unrelated setting", async () => {
    const mgr = managerWithStoredConfig({
      tray: { minimizeToTray: true, startMinimized: false, startOnLogin: 1 },
    });

    const result = await electronMock.ipcMain.simulateInvoke("tray:set-config", {
      tray: { minimizeToTray: false },
    });

    expect(result.tray).toEqual({ ...DEFAULT_TRAY, minimizeToTray: false });
    expect(JSON.parse(fs.writeFileSync.mock.calls[0][1]).tray.startOnLogin).toBe(false);
    expect(mgr.config.tray.minimizeToTray).toBe(false);
  });

  test("the renderer's get-config → set-config round-trip still saves", async () => {
    managerWithStoredConfig({
      tray: { minimizeToTray: true, startMinimized: null, startOnLogin: "yes" },
    });

    // The renderer edits whatever tray:get-config handed it and sends it back.
    const current = await electronMock.ipcMain.simulateInvoke("tray:get-config");
    const result = await electronMock.ipcMain.simulateInvoke("tray:set-config", {
      tray: { ...current.tray, startOnLogin: true },
    });

    expect(result.tray).toEqual({ ...DEFAULT_TRAY, startOnLogin: true });
    expect(electronMock.app.setLoginItemSettings).toHaveBeenCalledWith(
      expect.objectContaining({ openAtLogin: true })
    );
  });

  test("a non-object tray section on disk falls back to defaults", () => {
    const mgr = managerWithStoredConfig({ tray: "on" });
    expect(mgr.config.tray).toEqual(DEFAULT_TRAY);
  });

  test("a JSON file that is not an object falls back to defaults", () => {
    const mgr = managerWithStoredConfig("[1, 2, 3]");
    expect(mgr.config.tray).toEqual(DEFAULT_TRAY);
  });
});

describe("live state on the tray", () => {
  function liveTray(platform = "win32") {
    setPlatform(platform);
    const win = createMockWindow();
    const mgr = newTray(win);
    mgr.create();
    return { mgr, win };
  }

  const menuLabels = (mgr) => mgr.tray._contextMenu._template.map((i) => i.label || i.type);

  test.each([
    [activity(), "idle"],
    [activity({ running: [{ id: "a", title: "Trip plan" }] }), "working"],
    [activity({ running: [{ id: "a", title: "T" }], waitingOnModel: "Loading Gemma into memory" }), "waiting"],
    [activity({ running: [{ id: "a", title: "T" }], needsYou: [{ id: "a", title: "T", reason: "approval" }] }), "needs-you"],
    [activity({ backendConnected: false }), "error"],
    [activity({ modelServerRunning: false }), "error"],
  ])("a snapshot sets the icon to its state (%#)", (snapshot, state) => {
    const { mgr } = liveTray();
    mgr.setActivity(snapshot);
    expect(mgr.state).toBe(state);
    expect(mgr.tray._icon).toBe(mgr._iconFor(state));
  });

  test("each state has its own image", () => {
    const { mgr } = liveTray();
    const images = ["idle", "working", "waiting", "needs-you", "error"].map((s) => mgr._iconFor(s));
    expect(new Set(images).size).toBe(5);
  });

  test("the image is swapped only when the state changes", () => {
    const { mgr } = liveTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    mgr.setActivity(activity({ running: [{ id: "b", title: "B" }] }));
    expect(mgr.tray.setImage).toHaveBeenCalledTimes(1);
  });

  test("the tooltip says what is happening", () => {
    const { mgr } = liveTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "Trip plan" }] }));
    expect(mgr.tray._toolTip).toBe("GAIA — answering in 'Trip plan'");
    mgr.setActivity(
      activity({
        needsYou: [
          { id: "a", title: "A", reason: "approval" },
          { id: "b", title: "B", reason: "approval" },
        ],
      })
    );
    expect(mgr.tray._toolTip).toBe("GAIA — 2 chats need you");
  });

  test("the GAIA service exiting shows the error state", () => {
    const { mgr } = liveTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    mgr.setBackendAlive(false);
    expect(mgr.state).toBe("error");
    expect(mgr.tray._toolTip).toMatch(/GAIA service stopped/);
  });

  test("the menu is rebuilt when its text changes, so Linux trays redraw it", () => {
    const { mgr } = liveTray("linux");
    const before = mgr.tray.setContextMenu.mock.calls.length;
    mgr.setActivity(activity());
    mgr.setActivity(activity());
    expect(mgr.tray.setContextMenu.mock.calls.length).toBe(before + 1);
    expect(menuLabels(mgr)[0]).toBe("Gemma-4-E4B-it on GPU · idle");
  });

  test("tray:set-activity rejects a malformed snapshot and keeps the last good one", async () => {
    const { mgr } = liveTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    await expect(
      electronMock.ipcMain.simulateInvoke("tray:set-activity", { backendConnected: "yes" })
    ).rejects.toThrow(/backendConnected must be a boolean/);
    expect(mgr.state).toBe("working");
  });

  test("tray:set-activity drives the tray", async () => {
    const { mgr } = liveTray();
    await electronMock.ipcMain.simulateInvoke(
      "tray:set-activity",
      activity({ needsYou: [{ id: "a", title: "A", reason: "answer" }] })
    );
    expect(mgr.state).toBe("needs-you");
  });

  test("a Needs you item opens that chat in the window", () => {
    const { mgr, win } = liveTray();
    win.show = jest.fn();
    mgr.setActivity(activity({ needsYou: [{ id: "s-42", title: "Trip plan", reason: "approval" }] }));
    const item = mgr.tray._contextMenu._template.find((i) => i.label === "Trip plan — approve");
    item.click();
    expect(win.show).toHaveBeenCalled();
    expect(win.webContents.send).toHaveBeenCalledWith("tray:command", {
      type: "open-chat",
      sessionId: "s-42",
    });
  });

  test("New chat and Settings… reach the window", () => {
    const { mgr, win } = liveTray();
    mgr.setActivity(activity());
    const byLabel = (l) => mgr.tray._contextMenu._template.find((i) => i.label === l);
    byLabel("New chat").click();
    byLabel("Settings…").click();
    expect(win.webContents.send).toHaveBeenCalledWith("tray:command", { type: "new-chat" });
    expect(win.webContents.send).toHaveBeenCalledWith("tray:command", {
      type: "open-settings",
      section: "general",
    });
  });

  test("with the window closed, a menu action reopens it and runs once it reports", () => {
    const { mgr, win } = liveTray();
    mgr.setActivity(activity({ recent: [{ id: "s-7", title: "Old chat" }] }));
    win._isDestroyed = true;
    const fresh = createMockWindow();
    mgr._reopenWindow.mockReturnValue(fresh);

    mgr.tray._contextMenu._template.find((i) => i.label === "Old chat").click();
    expect(mgr._reopenWindow).toHaveBeenCalledTimes(1);
    expect(mgr.mainWindow).toBe(fresh);
    // Not yet: the new window's tray bridge isn't listening until it reports.
    expect(fresh.webContents.send).not.toHaveBeenCalled();

    mgr.setActivity(activity());
    expect(fresh.webContents.send).toHaveBeenCalledWith("tray:command", { type: "open-chat", sessionId: "s-7" });
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    expect(fresh.webContents.send).toHaveBeenCalledTimes(1);
  });

  test("Show GAIA brings back a closed window", () => {
    const { mgr, win } = liveTray();
    mgr.setActivity(activity());
    win._isDestroyed = true;
    mgr.tray._contextMenu._template.find((i) => i.label === "Show GAIA").click();
    expect(mgr._reopenWindow).toHaveBeenCalledTimes(1);
  });

  test("a closed window's last report no longer shows", () => {
    const { mgr } = liveTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    mgr.windowClosed();
    expect(mgr.state).toBe("idle");
    expect(mgr.tray._toolTip).toBe("GAIA — starting");
  });

  test("needs a way to reopen a closed window", () => {
    setPlatform("win32");
    expect(() => new TrayManager(createMockWindow())).toThrow(/needs options.reopenWindow/);
  });
});

describe("Windows taskbar overlay", () => {
  test("shows needs-you, then unread, then clears", () => {
    setPlatform("win32");
    const win = createMockWindow();
    const mgr = newTray(win);
    mgr.create();

    mgr.setActivity(activity({ needsYou: [{ id: "a", title: "A", reason: "approval" }] }));
    expect(win.setOverlayIcon).toHaveBeenLastCalledWith(mgr._overlayImage("needs-you"), "1 chat needs you");

    mgr.setActivity(activity());
    mgr.setNotificationCount(3);
    expect(win.setOverlayIcon).toHaveBeenLastCalledWith(mgr._overlayImage("unread"), "3 unread notifications");

    mgr.setNotificationCount(0);
    expect(win.setOverlayIcon).toHaveBeenLastCalledWith(null, "");
  });

  test("is not set again when nothing changed", () => {
    setPlatform("win32");
    const win = createMockWindow();
    const mgr = newTray(win);
    mgr.create();
    mgr.setNotificationCount(2);
    const calls = win.setOverlayIcon.mock.calls.length;
    mgr.setActivity(activity());
    expect(win.setOverlayIcon.mock.calls.length).toBe(calls);
  });

  test("is Windows-only", () => {
    setPlatform("darwin");
    const win = createMockWindow();
    const mgr = newTray(win);
    mgr.create();
    mgr.setNotificationCount(2);
    expect(win.setOverlayIcon).not.toHaveBeenCalled();
  });
});

describe("Free memory", () => {
  const realFetch = global.fetch;

  afterEach(() => {
    global.fetch = realFetch;
  });

  function freeMemoryTray() {
    setPlatform("win32");
    const win = createMockWindow();
    const mgr = newTray(win, { backendPort: 4321 });
    mgr.create();
    mgr.setActivity(activity());
    return { mgr, win };
  }

  const freeItem = (mgr) =>
    mgr.tray._contextMenu._template.find((i) => String(i.label).startsWith("Free memory"));

  test("asks first, unloads the model, and the status line shows it", async () => {
    const { mgr, win } = freeMemoryTray();
    electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 0 });
    global.fetch = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({ status: "unloaded" }) }));

    await mgr._freeMemory();

    expect(electronMock.dialog.showMessageBox.mock.calls[0][0].message).toBe(
      "Unload Gemma-4-E4B-it-GGUF from memory?"
    );
    expect(global.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:4321/api/system/unload-model",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "X-Gaia-UI": "1", "Content-Type": "application/json" }),
        body: JSON.stringify({ model_name: "Gemma-4-E4B-it-GGUF" }),
      })
    );
    expect(mgr.tray._contextMenu._template[0].label).toBe("No model loaded · idle");
    expect(freeItem(mgr).enabled).toBe(false);
    expect(win.webContents.send).toHaveBeenCalledWith("tray:command", { type: "refresh-status" });
  });

  test("does nothing when the user cancels", async () => {
    const { mgr } = freeMemoryTray();
    electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 1 });
    global.fetch = jest.fn();
    await mgr._freeMemory();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  test("shows the backend's reason when it refuses", async () => {
    const { mgr } = freeMemoryTray();
    electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 0 });
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ detail: "A document is still being indexed." }),
    }));
    await mgr._freeMemory();
    const errorBox = electronMock.dialog.showMessageBox.mock.calls[1][0];
    expect(errorBox.type).toBe("error");
    expect(errorBox.detail).toBe("A document is still being indexed.");
    expect(mgr.tray._contextMenu._template[0].label).toMatch(/^Gemma/);
  });

  test("is disabled while an answer is running", () => {
    const { mgr } = freeMemoryTray();
    mgr.setActivity(activity({ running: [{ id: "a", title: "A" }] }));
    expect(freeItem(mgr).enabled).toBe(false);
    expect(freeItem(mgr).label).toBe("Free memory — an answer is running");
  });
});

describe("first-close notice", () => {
  let disk;

  beforeEach(() => {
    disk = null;
    fs.existsSync.mockImplementation((p) => !String(p).endsWith("tray-config.json") || disk !== null);
    fs.readFileSync.mockImplementation(() => disk);
    fs.writeFileSync.mockImplementation((_p, data) => {
      disk = data;
    });
  });

  afterEach(() => {
    fs.existsSync.mockImplementation(() => true);
    fs.readFileSync.mockImplementation(() => "{}");
    fs.writeFileSync.mockImplementation(() => {});
  });

  test("shows once, and never again after a restart", async () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    expect(mgr.closeNoticeDue).toBe(true);
    await mgr.showCloseNotice();
    await mgr.showCloseNotice();
    expect(electronMock.dialog.showMessageBox).toHaveBeenCalledTimes(1);
    expect(electronMock.dialog.showMessageBox.mock.calls[0][0].message).toBe(
      "GAIA is still running in the tray."
    );

    const restarted = newTray(createMockWindow());
    expect(restarted.closeNoticeDue).toBe(false);
  });

  test("says menu bar on macOS", async () => {
    setPlatform("darwin");
    await newTray(createMockWindow()).showCloseNotice();
    expect(electronMock.dialog.showMessageBox.mock.calls[0][0].message).toBe(
      "GAIA is still running in the menu bar."
    );
  });

  test("Quit GAIA in the notice reports the user's choice", async () => {
    setPlatform("win32");
    electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 1 });
    expect(await newTray(createMockWindow()).showCloseNotice()).toBe(true);
  });

  test("saving tray settings keeps the notice marked as shown", async () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    await mgr.showCloseNotice();
    await electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { minimizeToTray: false } });
    expect(JSON.parse(disk).notices.firstClose).toBe(true);
  });
});

describe("start at login", () => {
  beforeEach(() => {
    mockLoginItems();
  });

  test("a refused login item fails the save and leaves the setting off", async () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    electronMock.app.setLoginItemSettings.mockImplementation(() => {
      throw new Error("access denied");
    });
    await expect(
      electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } })
    ).rejects.toThrow(/start at sign-in: access denied/);
    expect(fs.writeFileSync).not.toHaveBeenCalled();
    expect(mgr.startOnLogin).toBe(false);
  });

  test("an OS that quietly keeps the old setting fails the save", async () => {
    setPlatform("win32");
    const mgr = newTray(createMockWindow());
    electronMock.app.getLoginItemSettings.mockReturnValue({ openAtLogin: false });
    await expect(
      electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } })
    ).rejects.toThrow(/the system still reports it off/);
    expect(mgr.startOnLogin).toBe(false);
  });

  test("macOS waiting on the user's approval says where to give it", async () => {
    setPlatform("darwin");
    newTray(createMockWindow());
    electronMock.app.getLoginItemSettings.mockReturnValue({ openAtLogin: true, status: "requires-approval" });
    await expect(
      electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } })
    ).rejects.toThrow(/System Settings → General → Login Items/);
  });

  test("a failed save undoes the login item, so the toggle and the OS agree", async () => {
    setPlatform("win32");
    newTray(createMockWindow());
    fs.writeFileSync.mockImplementationOnce(() => {
      throw new Error("EACCES");
    });
    await expect(
      electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } })
    ).rejects.toThrow(/Could not save settings/);
    expect(electronMock.app.setLoginItemSettings.mock.calls.map((c) => c[0].openAtLogin)).toEqual([true, false]);
  });

  test("saving another setting leaves the login item alone", async () => {
    setPlatform("win32");
    newTray(createMockWindow());
    await electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startMinimized: true } });
    expect(electronMock.app.setLoginItemSettings).not.toHaveBeenCalled();
  });

  test("registers the app without forcing it minimized", async () => {
    setPlatform("darwin");
    newTray(createMockWindow());
    await electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } });
    expect(electronMock.app.setLoginItemSettings).toHaveBeenCalledWith(
      expect.objectContaining({ openAtLogin: true, args: [] })
    );
  });

  test("Linux writes an XDG autostart entry, and removes it again", async () => {
    setPlatform("linux");
    const path = require("path");
    const prevXdg = process.env.XDG_CONFIG_HOME;
    const prevAppImage = process.env.APPIMAGE;
    process.env.XDG_CONFIG_HOME = "/home/u/.config";
    process.env.APPIMAGE = "/home/u/Apps/GAIA $1 100%.AppImage";
    fs.unlinkSync = jest.fn();
    try {
      newTray(createMockWindow());
      await electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: true } });
      const entry = fs.writeFileSync.mock.calls.find((c) => String(c[0]).endsWith(".desktop"));
      expect(path.normalize(entry[0])).toBe(
        path.normalize("/home/u/.config/autostart/gaia-agent-ui.desktop")
      );
      // `$` is escaped inside the quotes, then the backslash is escaped again;
      // `%` would start a field code, so it is doubled.
      expect(entry[1]).toContain('Exec="/home/u/Apps/GAIA \\\\$1 100%%.AppImage"');
      expect(electronMock.app.setLoginItemSettings).not.toHaveBeenCalled();

      await electronMock.ipcMain.simulateInvoke("tray:set-config", { tray: { startOnLogin: false } });
      expect(fs.unlinkSync).toHaveBeenCalledWith(entry[0]);
    } finally {
      if (prevXdg === undefined) delete process.env.XDG_CONFIG_HOME;
      else process.env.XDG_CONFIG_HOME = prevXdg;
      if (prevAppImage === undefined) delete process.env.APPIMAGE;
      else process.env.APPIMAGE = prevAppImage;
    }
  });
});
