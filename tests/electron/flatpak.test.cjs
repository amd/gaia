// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * What GAIA does differently as a Flatpak (services/flatpak.cjs): it hides on
 * close where GNOME's Background Apps lists it, and it shares one directory
 * between launches so a second launch finds the first.
 */

const path = require("path");
const electronMock = require("electron");

jest.mock("fs", () => ({
  existsSync: jest.fn(() => true),
  readFileSync: jest.fn(() => "{}"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
}));

const {
  APP_ID,
  BACKGROUND_MONITOR,
  isFlatpak,
  isGnomeFlatpak,
  shareTmpDirAcrossLaunches,
} = require("../../src/gaia/apps/webui/services/flatpak.cjs");
const { probeTrayHost, WATCHER } = require("../../src/gaia/apps/webui/services/tray-host.cjs");
const TrayManager = require("../../src/gaia/apps/webui/services/tray-manager.cjs");

const FLATPAK = { FLATPAK_ID: "ai.amd.gaia", XDG_RUNTIME_DIR: "/run/user/1000" };
const GNOME = { ...FLATPAK, XDG_CURRENT_DESKTOP: "GNOME" };

/** A session bus where exactly these names are owned, asked through gdbus. */
function busOwning(...owned) {
  return jest.fn(async (cmd, args) => {
    if (cmd !== "gdbus") throw Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" });
    return owned.includes(args[args.length - 1]) ? "(true,)\n" : "(false,)\n";
  });
}

describe("isFlatpak", () => {
  test("is GAIA's own Flatpak, by the ID flatpak run sets inside the sandbox", () => {
    expect(APP_ID).toBe("ai.amd.gaia");
    expect(isFlatpak(FLATPAK)).toBe(true);
    expect(isFlatpak({})).toBe(false);
    expect(isFlatpak({ FLATPAK_ID: "" })).toBe(false);
  });

  test("another app's sandbox, such as a terminal in a Flatpak IDE, is not it", () => {
    expect(isFlatpak({ FLATPAK_ID: "com.visualstudio.code" })).toBe(false);
  });
});

describe("isGnomeFlatpak", () => {
  test.each([["GNOME"], ["ubuntu:GNOME"], ["gnome"]])("the Flatpak on %s", (desktop) => {
    expect(isGnomeFlatpak({ ...FLATPAK, XDG_CURRENT_DESKTOP: desktop })).toBe(true);
  });

  test.each([["KDE"], ["XFCE"], ["GNOME-Flashback"], [undefined]])("not on %s", (desktop) => {
    expect(isGnomeFlatpak({ ...FLATPAK, XDG_CURRENT_DESKTOP: desktop })).toBe(false);
  });

  test("not for the AppImage or .deb, which GNOME never lists", () => {
    expect(isGnomeFlatpak({ XDG_CURRENT_DESKTOP: "GNOME" })).toBe(false);
  });
});

describe("shareTmpDirAcrossLaunches", () => {
  test("points TMPDIR at the directory every launch of the Flatpak shares", () => {
    const env = { ...FLATPAK, TMPDIR: "/tmp" };
    const shared = path.join("/run/user/1000", "app", "ai.amd.gaia");
    expect(shareTmpDirAcrossLaunches(env)).toBe(shared);
    expect(env.TMPDIR).toBe(shared);
  });

  test.each([[{}], [{ FLATPAK_ID: "com.visualstudio.code" }]])("leaves TMPDIR alone outside GAIA's Flatpak", (extra) => {
    const env = { TMPDIR: "/tmp", XDG_RUNTIME_DIR: "/run/user/1000", ...extra };
    expect(shareTmpDirAcrossLaunches(env)).toBeNull();
    expect(env.TMPDIR).toBe("/tmp");
  });

  test("the Flatpak with no XDG_RUNTIME_DIR fails, naming what to do", () => {
    expect(() => shareTmpDirAcrossLaunches({ FLATPAK_ID: "ai.amd.gaia" })).toThrow(
      /XDG_RUNTIME_DIR is not set.*flatpak run ai\.amd\.gaia/s
    );
  });
});

describe("closing the Flatpak's window", () => {
  test("GNOME 44 or newer has no tray, but Background Apps lists the hidden app", async () => {
    const run = busOwning(BACKGROUND_MONITOR);
    const host = await probeTrayHost({ platform: "linux", run, env: GNOME });
    expect(host).toEqual({ available: true, reason: null, via: "background-apps" });
    expect(run.mock.calls.map(([, args]) => args[args.length - 1])).toEqual([WATCHER, BACKGROUND_MONITOR]);
  });

  test("an older GNOME has no Background Apps, so the window minimizes instead of hiding", async () => {
    const host = await probeTrayHost({ platform: "linux", run: busOwning(), env: GNOME });
    expect(host).toMatchObject({ available: false, reason: "no-host" });
  });

  test("a real tray wins, and Background Apps is never asked about", async () => {
    const run = busOwning(WATCHER, BACKGROUND_MONITOR);
    expect(await probeTrayHost({ platform: "linux", run, env: GNOME })).toEqual({ available: true, reason: null });
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("a bus that can't be asked never hides the window", async () => {
    const run = jest.fn(async () => {
      throw Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" });
    });
    const host = await probeTrayHost({ platform: "linux", run, env: GNOME });
    expect(host).toMatchObject({ available: false, reason: "check-failed" });
    expect(host.detail).toBe("gdbus: not installed; dbus-send: not installed");
  });

  test("the Flatpak on a desktop other than GNOME still minimizes", async () => {
    const env = { ...FLATPAK, XDG_CURRENT_DESKTOP: "XFCE" };
    const run = busOwning(BACKGROUND_MONITOR);
    expect(await probeTrayHost({ platform: "linux", run, env })).toMatchObject({ available: false, reason: "no-host" });
    expect(run).toHaveBeenCalledTimes(1);
  });

  test("the AppImage on GNOME still minimizes", async () => {
    const run = busOwning(BACKGROUND_MONITOR);
    const host = await probeTrayHost({ platform: "linux", run, env: { XDG_CURRENT_DESKTOP: "GNOME" } });
    expect(host).toMatchObject({ available: false, reason: "no-host" });
  });

  test("the window hides, and the notice says where GAIA went and how to stop it", () => {
    const mgr = new TrayManager(new electronMock.BrowserWindow(), { reopenWindow: jest.fn() });
    mgr.config.tray = { minimizeToTray: true, startMinimized: false, startOnLogin: false };
    mgr.setHost({ available: true, reason: null, via: "background-apps" });
    expect(mgr.closeAction).toBe("hide");
    expect(mgr._configReply().host).toEqual({ available: true, reason: null, via: "background-apps" });
    const { message, detail } = mgr.closeNoticeText();
    expect(message).toBe("GAIA is still running in the background.");
    expect(detail).toMatch(/Open GAIA again from your apps/);
    expect(detail).toMatch(/Background Apps/);
    expect(`${message} ${detail}`).not.toMatch(/tray/i);
  });
});
