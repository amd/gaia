// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Whether closing the window may hide GAIA to the tray: the tray-host probe
 * (services/tray-host.cjs) with a mocked session bus, TrayManager's gating on
 * its answer, and the close handler that follows it. Stock GNOME has no tray
 * host, so there closing must quit rather than strand the app.
 */

const { EventEmitter } = require("events");
const electronMock = require("electron");

jest.mock("fs", () => ({
  existsSync: jest.fn(() => true),
  readFileSync: jest.fn(() => "{}"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));

const { probeTrayHost, WATCHER } = require("../../src/gaia/apps/webui/services/tray-host.cjs");
const TrayManager = require("../../src/gaia/apps/webui/services/tray-manager.cjs");
const { attachWindowGuards } = require("../../src/gaia/apps/webui/services/quit-guard.cjs");

const ORIGINAL_PLATFORM = process.platform;
const setPlatform = (platform) => Object.defineProperty(process, "platform", { value: platform });

afterEach(() => setPlatform(ORIGINAL_PLATFORM));

const enoent = (cmd) => Object.assign(new Error(`spawn ${cmd} ENOENT`), { code: "ENOENT" });

/** A session bus answering NameHasOwner per client: a reply string, or an error to throw. */
function bus(replies) {
  return jest.fn(async (cmd) => {
    const reply = replies[cmd];
    if (reply instanceof Error) throw reply;
    if (reply === undefined) throw enoent(cmd);
    return reply;
  });
}

const DBUS_SEND = (owned) => `method return time=1 sender=org.freedesktop.DBus\n   boolean ${owned}\n`;

describe("probeTrayHost", () => {
  test.each(["win32", "darwin"])("%s always has a tray, without asking any bus", async (platform) => {
    const run = bus({});
    expect(await probeTrayHost({ platform, run })).toEqual({ available: true, reason: null });
    expect(run).not.toHaveBeenCalled();
  });

  test("KDE, or GNOME with the AppIndicator extension: the watcher is owned", async () => {
    const run = bus({ gdbus: "(true,)\n" });
    expect(await probeTrayHost({ platform: "linux", run })).toEqual({ available: true, reason: null });
    const [cmd, args] = run.mock.calls[0];
    expect(cmd).toBe("gdbus");
    expect(args).toEqual(expect.arrayContaining(["--session", "org.freedesktop.DBus.NameHasOwner", WATCHER]));
  });

  test("stock GNOME: nothing owns the watcher, so there is no tray", async () => {
    const host = await probeTrayHost({ platform: "linux", run: bus({ gdbus: "(false,)\n" }) });
    expect(host).toMatchObject({ available: false, reason: "no-host" });
    expect(host.detail).toContain(WATCHER);
  });

  test("without gdbus, dbus-send answers instead", async () => {
    const run = bus({ "dbus-send": DBUS_SEND("false") });
    expect(await probeTrayHost({ platform: "linux", run })).toMatchObject({ available: false, reason: "no-host" });
    expect(run.mock.calls.map(([cmd]) => cmd)).toEqual(["gdbus", "dbus-send"]);
    expect(run.mock.calls[1][1]).toContain(`string:${WATCHER}`);
  });

  test("dbus-send reporting the watcher owned means a tray", async () => {
    const run = bus({ "dbus-send": DBUS_SEND("true") });
    expect(await probeTrayHost({ platform: "linux", run })).toEqual({ available: true, reason: null });
  });

  test("no bus client at all: treated as no tray, and says why", async () => {
    const host = await probeTrayHost({ platform: "linux", run: bus({}) });
    expect(host).toMatchObject({ available: false, reason: "check-failed" });
    expect(host.detail).toBe("gdbus: not installed; dbus-send: not installed");
  });

  test("a bus error or an unreadable reply is a failed check, never a tray", async () => {
    const busDown = Object.assign(new Error("exit 1"), { stderr: "Cannot autolaunch D-Bus without X11 $DISPLAY\n" });
    const host = await probeTrayHost({ platform: "linux", run: bus({ gdbus: busDown, "dbus-send": "garbage" }) });
    expect(host).toMatchObject({ available: false, reason: "check-failed" });
    expect(host.detail).toContain("Cannot autolaunch D-Bus");
    expect(host.detail).toContain('unexpected reply "garbage"');
  });
});

describe("TrayManager without a tray host", () => {
  const newTray = () => new TrayManager(new electronMock.BrowserWindow(), { reopenWindow: jest.fn() });
  const ON = { minimizeToTray: true, startMinimized: true, startOnLogin: false };

  test("on Linux, nothing hides until the host is known", () => {
    setPlatform("linux");
    const mgr = newTray();
    mgr.config.tray = { ...ON };
    expect(mgr.trayHost).toEqual({ available: false, reason: "checking" });
    expect(mgr.minimizeToTray).toBe(false);
    expect(mgr.startMinimized).toBe(false);
    expect(mgr.closeAction).toBe("minimize");
    mgr.setHost({ available: true, reason: null });
    expect(mgr.minimizeToTray).toBe(true);
    expect(mgr.startMinimized).toBe(true);
    expect(mgr.closeAction).toBe("hide");
  });

  test("no host: closing minimizes, start-hidden is off, the saved choice is kept", async () => {
    setPlatform("linux");
    const mgr = newTray();
    mgr.config.tray = { ...ON };
    mgr.setHost({ available: false, reason: "no-host", detail: "nothing owns it" });
    expect(mgr.minimizeToTray).toBe(false);
    expect(mgr.closeAction).toBe("minimize");
    expect(mgr.startMinimized).toBe(false);
    const cfg = await electronMock.ipcMain.simulateInvoke("tray:get-config");
    expect(cfg.tray).toEqual(ON);
    expect(cfg.host).toEqual({ available: false, reason: "no-host" });
  });

  test.each([
    [{ available: true, reason: null }, "Tray icon host available"],
    [{ available: false, reason: "no-host", detail: "nothing owns it" }, "No tray icon host (no-host: nothing owns it)"],
  ])("the main log records the host either way: %j", (host, line) => {
    setPlatform("linux");
    const log = jest.spyOn(console, "log").mockImplementation(() => {});
    try {
      newTray().setHost(host);
      expect(log).toHaveBeenCalledWith(expect.stringContaining(line));
    } finally {
      log.mockRestore();
    }
  });

  test("Windows has a host from the start, and Settings is told so", async () => {
    setPlatform("win32");
    const mgr = newTray();
    expect(mgr.minimizeToTray).toBe(true);
    const cfg = await electronMock.ipcMain.simulateInvoke("tray:get-config");
    expect(cfg.host).toEqual({ available: true, reason: null });
  });

  test.each([["no-host"], ["check-failed"]])("with keep-running off, closing quits (%s)", (reason) => {
    setPlatform("linux");
    const mgr = newTray();
    mgr.config.tray = { ...ON, minimizeToTray: false };
    mgr.setHost({ available: false, reason });
    expect(mgr.closeAction).toBe("quit");
  });

  test("the notice points at the dock or overview, not a tray that isn't there", () => {
    setPlatform("linux");
    const mgr = newTray();
    mgr.config.tray = { ...ON };
    mgr.setHost({ available: false, reason: "no-host" });
    const { message, detail } = mgr.closeNoticeText();
    expect(message).toBe("GAIA is still running, minimized.");
    // Fedora's GNOME shows no dock outside the overview.
    expect(detail).toMatch(/dock or the Activities overview/);
    expect(detail).toMatch(/right-click GAIA there and choose Quit/);
    expect(`${message} ${detail}`).not.toMatch(/tray|AppIndicator/i);
  });

  test("the first-close notice waits for the tray check, so it names the right place", () => {
    setPlatform("linux");
    const mgr = newTray();
    mgr.config.notices = { firstClose: false };
    expect(mgr.closeNoticeDue).toBe(false);
    mgr.setHost({ available: true, reason: null });
    expect(mgr.closeNoticeDue).toBe(true);
  });

  test("a malformed host result is refused loudly", () => {
    setPlatform("linux");
    expect(() => newTray().setHost({ reason: "no-host" })).toThrow(/setHost expects/);
  });
});

describe("closing the window on a desktop without a tray", () => {
  function closeWith(mgr) {
    const win = new EventEmitter();
    win.hide = jest.fn();
    win.minimize = jest.fn();
    win.isMinimized = jest.fn(() => false);
    win.isFocused = jest.fn(() => true);
    const app = { quit: jest.fn() };
    attachWindowGuards(win, {
      app,
      guard: { confirmed: false, allow: jest.fn() },
      getTray: () => mgr,
      isQuitting: () => false,
      platform: "linux",
      reportNoticeError: jest.fn(),
    });
    const ev = { preventDefault: jest.fn() };
    win.emit("close", ev);
    return { win, app };
  }

  test("minimizes and keeps running instead of quitting", () => {
    setPlatform("linux");
    const mgr = new TrayManager(new electronMock.BrowserWindow(), { reopenWindow: jest.fn() });
    mgr.setHost({ available: false, reason: "no-host" });
    jest.spyOn(mgr, "showCloseNotice").mockResolvedValue(false);
    const { win, app } = closeWith(mgr);
    expect(win.hide).not.toHaveBeenCalled();
    expect(win.minimize).toHaveBeenCalledTimes(1);
    expect(app.quit).not.toHaveBeenCalled();
  });

  test("hides to the tray once a host is there", () => {
    setPlatform("linux");
    const mgr = new TrayManager(new electronMock.BrowserWindow(), { reopenWindow: jest.fn() });
    mgr.setHost({ available: true, reason: null });
    jest.spyOn(mgr, "showCloseNotice").mockResolvedValue(false);
    const { win, app } = closeWith(mgr);
    expect(win.hide).toHaveBeenCalledTimes(1);
    expect(app.quit).not.toHaveBeenCalled();
  });
});
