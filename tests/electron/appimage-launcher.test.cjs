// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// An AppImage installs no .desktop file, so GNOME has nothing to tie GAIA's
// window to. These run the launcher writer against a real folder.

const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  installAppImageLauncher,
  desktopExecArg,
  launcherPaths,
  OWNED,
} = require("../../src/gaia/apps/webui/services/appimage-launcher.cjs");
const pkg = require("../../src/gaia/apps/webui/package.json");

let home;
let appImage;
let deps;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-launcher-"));
  appImage = path.join(home, "Apps", "gaia-agent-ui-1.0.0-x86_64.AppImage");
  // No system data dir holds GAIA's entry, as on a PC without the .deb.
  deps = { platform: "linux", home, env: { APPIMAGE: appImage, XDG_DATA_DIRS: path.join(home, "sys") } };
});

afterEach(() => fs.rmSync(home, { recursive: true, force: true }));

const entryPath = () => launcherPaths(deps.env, home).entry;
const lines = () => fs.readFileSync(entryPath(), "utf8").split("\n");

test("an AppImage launch writes a launcher named after the window's app_id", () => {
  const result = installAppImageLauncher(deps);
  expect(result).toEqual({ status: "written", entry: entryPath() });
  expect(path.basename(entryPath())).toBe(pkg.desktopName);
  expect(entryPath()).toBe(path.join(home, ".local", "share", "applications", pkg.desktopName));

  const { icon } = launcherPaths(deps.env, home);
  expect(lines()).toEqual(
    expect.arrayContaining([
      "[Desktop Entry]",
      "Type=Application",
      "Name=GAIA",
      `Exec=${desktopExecArg(appImage)}`,
      `TryExec=${appImage.replace(/\\/g, "\\\\")}`,
      `Icon=${icon.replace(/\\/g, "\\\\")}`,
      `StartupWMClass=${pkg.desktopName.replace(/\.desktop$/, "")}`,
    ])
  );
  // A real PNG, not an empty file the desktop would draw as a blank square.
  expect(fs.readFileSync(icon).subarray(1, 4).toString()).toBe("PNG");
});

test("XDG_DATA_HOME moves the launcher", () => {
  deps.env.XDG_DATA_HOME = path.join(home, "data");
  installAppImageLauncher(deps);
  expect(fs.existsSync(path.join(home, "data", "applications", pkg.desktopName))).toBe(true);
});

test("a second launch leaves a current launcher untouched", () => {
  installAppImageLauncher(deps);
  const before = fs.statSync(entryPath()).mtimeMs;
  expect(installAppImageLauncher(deps).status).toBe("current");
  expect(fs.statSync(entryPath()).mtimeMs).toBe(before);
});

test("a moved AppImage gets its launcher pointed at the new place", () => {
  installAppImageLauncher(deps);
  const moved = path.join(home, "Elsewhere", "gaia.AppImage");
  deps.env.APPIMAGE = moved;
  expect(installAppImageLauncher(deps).status).toBe("written");
  expect(lines()).toContain(`Exec=${desktopExecArg(moved)}`);
  expect(lines()).not.toContain(`Exec=${desktopExecArg(appImage)}`);
});

test("a deleted icon is put back", () => {
  installAppImageLauncher(deps);
  const { icon } = launcherPaths(deps.env, home);
  fs.rmSync(icon);
  expect(installAppImageLauncher(deps).status).toBe("written");
  expect(fs.existsSync(icon)).toBe(true);
});

test("a launcher someone else wrote is never replaced", () => {
  fs.mkdirSync(path.dirname(entryPath()), { recursive: true });
  const theirs = "[Desktop Entry]\nName=GAIA\nExec=/opt/mine/gaia\n";
  fs.writeFileSync(entryPath(), theirs);
  expect(installAppImageLauncher(deps).status).toBe("kept-existing");
  expect(fs.readFileSync(entryPath(), "utf8")).toBe(theirs);
  expect(theirs).not.toContain(OWNED);
});

const writeOther = (name, text) => {
  const file = path.join(path.dirname(entryPath()), name);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text);
  return file;
};

test("an entry another tool wrote for this AppImage stops GAIA writing a second", () => {
  const theirs = writeOther("appimagekit_abc-GAIA.desktop", `[Desktop Entry]\nName=GAIA\nExec=${appImage} %U\n`);
  expect(installAppImageLauncher(deps)).toEqual({ status: "kept-existing", entry: theirs });
  expect(fs.existsSync(entryPath())).toBe(false);
  expect(fs.existsSync(theirs)).toBe(true);
});

test("the entry Gear Lever writes for this AppImage stops GAIA writing a second", () => {
  // The second launcher rc15 and rc16 left behind.
  expect(installAppImageLauncher(deps).status).toBe("written");
  // Copied from Gear Lever on Fedora 44: Exec= starts with `env`, so only TryExec= names the file first.
  const theirs = writeOther(
    "gaia.desktop",
    [
      "[Desktop Entry]",
      "Type=Application",
      "Name=GAIA",
      `Icon=${path.join(home, "AppImages", ".icons", "gaia")}`,
      `TryExec=${appImage}`,
      `Exec=env DESKTOPINTEGRATION=1 ${appImage} --no-sandbox %U`,
      "StartupWMClass=gaia-desktop",
      "X-AppImage-Version=0.26.0-rc.16",
      "",
    ].join("\n")
  );
  expect(installAppImageLauncher(deps)).toEqual({ status: "kept-existing", entry: theirs });
  expect(fs.existsSync(entryPath())).toBe(false);
});

test("a quoted, escaped Exec= or a TryExec= still matches this AppImage", () => {
  deps.env.APPIMAGE = path.join(home, "My Apps", "50% gaia.AppImage");
  writeOther("quoted.desktop", `[Desktop Entry]\nExec=${desktopExecArg(deps.env.APPIMAGE)} --flag\n`);
  expect(installAppImageLauncher(deps).status).toBe("kept-existing");
  fs.rmSync(path.join(path.dirname(entryPath()), "quoted.desktop"));
  writeOther("tryexec.desktop", `[Desktop Entry]\nExec=env FOO=1 gaia\nTryExec=${deps.env.APPIMAGE}\n`);
  expect(installAppImageLauncher(deps).status).toBe("kept-existing");
});

test("an entry that runs the AppImage through a symlink counts as the same app", () => {
  fs.mkdirSync(path.dirname(appImage), { recursive: true });
  fs.writeFileSync(appImage, "");
  const link = path.join(home, "gaia.AppImage");
  fs.symlinkSync(appImage, link);
  writeOther("gaia.desktop", `[Desktop Entry]\nExec=${link}\n`);
  expect(installAppImageLauncher(deps).status).toBe("kept-existing");
});

test("GAIA's own launcher is removed once another entry for the AppImage appears", () => {
  installAppImageLauncher(deps);
  const theirs = writeOther("gearlever_gaia.desktop", `[Desktop Entry]\nExec="${appImage}"\n`);
  expect(installAppImageLauncher(deps)).toEqual({ status: "kept-existing", entry: theirs });
  expect(fs.existsSync(entryPath())).toBe(false);
  expect(fs.existsSync(theirs)).toBe(true);
});

test("entries for other apps, or another GAIA build, don't stop the launcher", () => {
  writeOther("firefox.desktop", "[Desktop Entry]\nExec=firefox %u\n");
  writeOther("old-gaia.desktop", `[Desktop Entry]\nExec=${path.join(home, "Apps", "gaia-agent-ui-0.9.0-x86_64.AppImage")}\n`);
  fs.symlinkSync(path.join(home, "missing"), path.join(path.dirname(entryPath()), "dangling.desktop"));
  expect(installAppImageLauncher(deps).status).toBe("written");
});

test("another app's entry GAIA can't read or follow doesn't stop the launcher", () => {
  const locked = writeOther("locked.desktop", "[Desktop Entry]\nExec=locked\n");
  writeOther("looped.desktop", "[Desktop Entry]\nExec=/loop/bin/app\n");
  const denied = (code) => Object.assign(new Error(code), { code });
  const read = fs.readFileSync;
  const realpath = fs.realpathSync;
  const readSpy = jest.spyOn(fs, "readFileSync").mockImplementation((file, ...rest) => {
    if (file === locked) throw denied("EACCES");
    return read(file, ...rest);
  });
  const realpathSpy = jest.spyOn(fs, "realpathSync").mockImplementation((p, ...rest) => {
    if (String(p).startsWith("/loop/")) throw denied("ELOOP");
    return realpath(p, ...rest);
  });
  try {
    expect(installAppImageLauncher(deps).status).toBe("written");
  } finally {
    readSpy.mockRestore();
    realpathSpy.mockRestore();
  }
});

test("with the .deb's launcher installed, the AppImage writes none over it", () => {
  const system = path.join(home, "sys", "applications");
  fs.mkdirSync(system, { recursive: true });
  fs.writeFileSync(path.join(system, pkg.desktopName), "[Desktop Entry]\n");
  expect(installAppImageLauncher(deps).status).toBe("package-installed");
  expect(fs.existsSync(entryPath())).toBe(false);
});

test("the AppImage's own bundled entry on XDG_DATA_DIRS doesn't count as an install", () => {
  const appDir = path.join(home, "mount");
  const bundled = path.join(appDir, "usr", "share", "applications");
  fs.mkdirSync(bundled, { recursive: true });
  fs.writeFileSync(path.join(bundled, pkg.desktopName), "[Desktop Entry]\n");
  deps.env.APPDIR = appDir;
  deps.env.XDG_DATA_DIRS = `${path.join(appDir, "usr", "share")}:${deps.env.XDG_DATA_DIRS}`;
  expect(installAppImageLauncher(deps).status).toBe("written");
});

test("a release with a new icon replaces the old one", () => {
  installAppImageLauncher(deps);
  const { icon } = launcherPaths(deps.env, home);
  fs.writeFileSync(icon, "last release's icon");
  expect(installAppImageLauncher(deps).status).toBe("written");
  expect(fs.readFileSync(icon).subarray(1, 4).toString()).toBe("PNG");
});

test("a newline in the AppImage path stays on one line of the entry", () => {
  deps.env.APPIMAGE = `${home}/odd\nname.AppImage`;
  installAppImageLauncher(deps);
  const tryExec = lines().filter((line) => line.startsWith("TryExec="));
  expect(tryExec).toHaveLength(1);
  expect(tryExec[0].endsWith("odd\\nname.AppImage")).toBe(true);
  expect(lines().find((line) => line.startsWith("Exec=")).endsWith('odd\\nname.AppImage"')).toBe(true);
  expect(lines()).toContain("StartupWMClass=gaia-desktop");
  expect(lines().every((line) => line === "" || line.startsWith("[") || line.includes("="))).toBe(true);
});

test.each([
  ["Windows", { platform: "win32" }],
  ["a Linux .deb install, which has no APPIMAGE", { env: {} }],
])("%s writes nothing", (_name, override) => {
  expect(installAppImageLauncher({ ...deps, ...override }).status).toBe("not-appimage");
  expect(fs.existsSync(path.join(home, ".local"))).toBe(false);
});

test("a folder that can't be written says which file and what still works", () => {
  // A file where the applications folder should be makes mkdir fail on any OS.
  fs.mkdirSync(path.join(home, ".local", "share"), { recursive: true });
  fs.writeFileSync(path.join(home, ".local", "share", "applications"), "");
  expect(() => installAppImageLauncher(deps)).toThrow(/Could not write GAIA's launcher .*gaia-desktop\.desktop.*GAIA still runs/s);
});

test("a path with spaces, quotes and % survives Exec= quoting", () => {
  expect(desktopExecArg('/home/a b/50% "x"/g.AppImage')).toBe('"/home/a b/50%% \\\\"x\\\\"/g.AppImage"');
});
