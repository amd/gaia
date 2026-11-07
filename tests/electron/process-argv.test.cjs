// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The launcher and installer pass process arguments as arrays with no shell,
// so a URL or path is handed over whole however it is spelled (#2950).
// Nothing here runs a real process: every child_process call is a jest.fn.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const CLI = path.join(__dirname, "../../src/gaia/apps/webui/bin/gaia-ui.cjs");
const INSTALLER = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

const AWKWARD = `My Files "quoted" & $(x) ; 'single' | %PATH% \`tick\``;

function fakeChild() {
  const child = new EventEmitter();
  child.unref = jest.fn();
  return child;
}

describe("gaia-ui opens the browser without a shell", () => {
  const { browserCommand, openBrowser } = require(CLI);
  const url = `http://localhost:4200/?q=${AWKWARD}`;

  test.each([
    ["win32", "rundll32", ["url.dll,FileProtocolHandler", url]],
    ["darwin", "open", [url]],
    ["linux", "xdg-open", [url]],
  ])("on %s the URL is one untouched argument", (platform, file, args) => {
    expect(browserCommand(url, platform)).toEqual([file, args]);
  });

  test("spawns with shell: false and the argv array", () => {
    const spawnFn = jest.fn(() => fakeChild());
    openBrowser(url, { spawnFn, platform: "win32" });

    expect(spawnFn).toHaveBeenCalledTimes(1);
    const [file, args, opts] = spawnFn.mock.calls[0];
    expect(file).toBe("rundll32");
    expect(args).toEqual(["url.dll,FileProtocolHandler", url]);
    expect(opts.shell).toBe(false);
  });

  test("a failed launch tells the user to open the URL by hand", () => {
    const child = fakeChild();
    openBrowser(url, { spawnFn: () => child, platform: "linux" });
    child.emit("error", new Error("spawn xdg-open ENOENT"));
    expect(console.log).toHaveBeenCalledWith(`  Open manually: ${url}`);
  });
});

function loadInstaller({ platform, home }) {
  const p = platform === "win32" ? path.win32 : path.posix;
  const spawnSync = jest.fn(() => ({ status: 1 }));
  const execFileSync = jest.fn(() => "0\n");
  const fakeFs = {
    ...fs,
    existsSync: () => false,
    mkdirSync: jest.fn(),
    statfsSync: undefined,
    createWriteStream: () => ({ write: jest.fn(), end: jest.fn(), on: jest.fn() }),
    appendFileSync: jest.fn(),
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(INSTALLER),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform, arch: "x64", env: { KEEP: "1" } },
    setTimeout,
    clearTimeout,
    queueMicrotask,
    URL,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return p;
      if (name === "os") return { ...require("os"), homedir: () => home };
      if (name === "child_process") return { spawn: jest.fn(), spawnSync, execFileSync };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(INSTALLER, "utf8"), context, { filename: INSTALLER });
  return { installer: context.module.exports, spawnSync, execFileSync };
}

describe("the backend installer runs its probes without a shell", () => {
  test.each([
    ["win32", "where"],
    ["linux", "which"],
  ])("on %s the PATH lookup passes the name as its own argument", (platform, file) => {
    const home = platform === "win32" ? "C:\\Users\\a" : "/home/a";
    const { installer, spawnSync } = loadInstaller({ platform, home });

    expect(installer.findGaiaBin()).toBeNull();
    expect(spawnSync).toHaveBeenCalledWith(file, ["gaia"], expect.objectContaining({ shell: false }));
  });

  test("df gets a home path with spaces and metacharacters as one argument", () => {
    const home = path.posix.join("/home", AWKWARD, "user");
    const { installer, execFileSync } = loadInstaller({ platform: "linux", home });

    installer.checkDiskSpace();

    expect(execFileSync).toHaveBeenCalledTimes(1);
    const [file, args, opts] = execFileSync.mock.calls[0];
    expect(file).toBe("df");
    expect(args).toEqual(["-k", path.posix.dirname(path.posix.join(home, ".gaia"))]);
    expect(opts.shell).toBeUndefined();
  });

  test("PowerShell's script is fixed text and the drive arrives through the environment", () => {
    const home = `C:\\Users\\${AWKWARD}`;
    const { installer, execFileSync } = loadInstaller({ platform: "win32", home });

    installer.checkDiskSpace();

    expect(execFileSync).toHaveBeenCalledTimes(1);
    const [file, args, opts] = execFileSync.mock.calls[0];
    expect(file).toBe("powershell");
    expect(args).toEqual(["-NoProfile", "-Command", "(Get-PSDrive -Name $env:GAIA_DISK_DRIVE).Free"]);
    expect(args.join(" ")).not.toContain(AWKWARD);
    expect(opts.env.GAIA_DISK_DRIVE).toBe("C");
    expect(opts.env.KEEP).toBe("1");
  });
});
