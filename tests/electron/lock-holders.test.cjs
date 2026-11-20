// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Which processes hold GAIA's files open, and which of them GAIA may close (#6570).

"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  queryLockHolders,
  summarizeHolders,
  endProcessTrees,
  isGaiaExecutable,
  startedFrom,
} = require("../../src/gaia/apps/webui/services/lock-holders.cjs");

const OWN_PID = 500;
const proc = (pid, p) => ({ pid, name: p.split("\\").pop(), path: p, commandLine: p });
const DEMO_GAIA = "C:\\Users\\you\\demo-home\\.gaia\\venv\\Scripts\\gaia.exe";
const BASE_PYTHON = "C:\\Users\\you\\AppData\\Roaming\\uv\\python\\cpython-3.12\\python.exe";

describe("isGaiaExecutable", () => {
  test.each([
    DEMO_GAIA,
    "C:\\Users\\you\\.gaia\\agents\\gaia\\gaia-agent.exe",
    "C:\\Users\\you\\.gaia\\bin\\lemond.exe",
    "D:/other/home/.gaia/venv/Scripts/uvicorn.exe",
    "C:\\Program Files\\GAIA\\gaia-desktop.exe",
    "C:\\anywhere\\gaia-agent.exe",
  ])("%s is GAIA", (p) => expect(isGaiaExecutable(p)).toBe(true));

  test.each([
    BASE_PYTHON,
    "C:\\Program Files\\VS Code\\Code.exe",
    "C:\\Users\\you\\.gaia\\cache\\tool.exe",
    "C:\\Users\\you\\gaia\\venv\\Scripts\\gaia.exe",
    null,
  ])("%s is not", (p) => expect(isGaiaExecutable(p)).toBe(false));
});

describe("startedFrom", () => {
  test("is the folder holding .gaia", () => {
    expect(startedFrom(DEMO_GAIA)).toBe("C:\\Users\\you\\demo-home");
  });
  test("is the program's own folder outside .gaia", () => {
    expect(startedFrom("C:\\Program Files\\VS Code\\Code.exe")).toBe("C:\\Program Files\\VS Code");
  });
  test("is null without a path", () => {
    expect(startedFrom(null)).toBeNull();
  });
});

describe("summarizeHolders", () => {
  test("a GAIA launcher and its python child read as one copy of GAIA", () => {
    const launcher = proc(32852, DEMO_GAIA);
    const raw = [
      { ...launcher, ancestors: [proc(9000, "C:\\Windows\\explorer.exe")] },
      { ...proc(32900, BASE_PYTHON), ancestors: [launcher, proc(9000, "C:\\Windows\\explorer.exe")] },
    ];
    expect(summarizeHolders(raw, OWN_PID)).toEqual([
      { pid: 32852, name: "gaia.exe", path: DEMO_GAIA, folder: "C:\\Users\\you\\demo-home", isGaia: true },
    ]);
  });

  test("a program that isn't GAIA is reported as itself", () => {
    const raw = [{ ...proc(4100, "C:\\Program Files\\VS Code\\Code.exe"), ancestors: [] }];
    expect(summarizeHolders(raw, OWN_PID)).toEqual([
      { pid: 4100, name: "Code.exe", path: "C:\\Program Files\\VS Code\\Code.exe", folder: "C:\\Program Files\\VS Code", isGaia: false },
    ]);
  });

  test("this app's own process tree is never offered for closing", () => {
    const own = proc(OWN_PID, "C:\\Program Files\\GAIA\\gaia-desktop.exe");
    const raw = [
      { ...own, ancestors: [] },
      { ...proc(600, "C:\\Users\\you\\.gaia\\venv\\Scripts\\gaia.exe"), ancestors: [own] },
    ];
    expect(summarizeHolders(raw, OWN_PID).map((h) => [h.pid, h.isGaia])).toEqual([
      [OWN_PID, false],
      [600, true],
    ]);
  });

  test("a non-GAIA child of this app is not GAIA either", () => {
    const own = proc(OWN_PID, "C:\\Program Files\\GAIA\\gaia-desktop.exe");
    const raw = [{ ...proc(700, BASE_PYTHON), ancestors: [own] }];
    expect(summarizeHolders(raw, OWN_PID)[0]).toMatchObject({ pid: 700, isGaia: false });
  });

  test("a process Windows won't describe is named by its image", () => {
    const raw = [{ pid: 800, name: "gaia-agent.exe", path: null, commandLine: null, ancestors: [] }];
    expect(summarizeHolders(raw, OWN_PID)).toEqual([
      { pid: 800, name: "gaia-agent.exe", path: null, folder: null, isGaia: true },
    ]);
  });
});

describe("queryLockHolders", () => {
  test("hands the files to PowerShell through the environment and parses its JSON", async () => {
    const execFile = jest.fn((cmd, args, opts, cb) => cb(null, '[{"pid":1,"name":"a.exe","path":null,"ancestors":[]}]', ""));
    const files = ["C:\\x\\.gaia\\venv\\Scripts\\gaia.exe"];

    await expect(queryLockHolders(files, { execFile })).resolves.toEqual([
      { pid: 1, name: "a.exe", path: null, ancestors: [] },
    ]);
    const [cmd, args, opts] = execFile.mock.calls[0];
    expect(cmd).toBe("powershell.exe");
    expect(args).toContain("-EncodedCommand");
    expect(JSON.parse(opts.env.GAIA_LOCKED_FILES)).toEqual(files);
    expect(opts.timeout).toBeGreaterThan(0);
  });

  test("a failed lookup rejects with PowerShell's reason", async () => {
    const execFile = jest.fn((cmd, args, opts, cb) => cb(new Error("exit 1"), "", "RmRegisterResources failed with 5"));
    await expect(queryLockHolders(["C:\\f"], { execFile })).rejects.toThrow(/RmRegisterResources failed with 5/);
  });

  test("output that isn't JSON rejects", async () => {
    const execFile = jest.fn((cmd, args, opts, cb) => cb(null, "Add-Type: compile error", ""));
    await expect(queryLockHolders(["C:\\f"], { execFile })).rejects.toThrow(/other than JSON/);
  });

  (process.platform === "win32" ? test : test.skip)(
    "on Windows, names a real process holding a file open",
    async () => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-lock-"));
      const file = path.join(dir, "held.bin");
      fs.writeFileSync(file, "x");
      const holder = childProcess.spawn(
        process.execPath,
        ["-e", `require('fs').openSync(${JSON.stringify(file)}, 'r'); process.stdout.write('open'); setInterval(() => {}, 1000)`],
        { stdio: ["ignore", "pipe", "ignore"] }
      );
      try {
        await new Promise((resolve) => holder.stdout.once("data", resolve));
        const raw = await queryLockHolders([file]);
        const found = raw.find((h) => h.pid === holder.pid);
        expect(found).toBeDefined();
        expect(found.ancestors.map((a) => a.pid)).toContain(process.pid);
        expect(summarizeHolders(raw, process.pid)).toEqual([expect.objectContaining({ pid: holder.pid, isGaia: false })]);
      } finally {
        holder.kill();
        fs.rmSync(dir, { recursive: true, force: true });
      }
    },
    60000
  );
});

describe("endProcessTrees", () => {
  test("ends each pid with its children", async () => {
    const execFile = jest.fn((cmd, args, opts, cb) => cb(null, "", ""));
    await endProcessTrees([11, 22], { execFile });
    expect(execFile.mock.calls.map(([cmd, args]) => [cmd, ...args])).toEqual([
      ["taskkill", "/PID", "11", "/T", "/F"],
      ["taskkill", "/PID", "22", "/T", "/F"],
    ]);
  });

  test("a process that already exited is fine", async () => {
    const gone = Object.assign(new Error("exit 128"), { code: 128 });
    const execFile = jest.fn((cmd, args, opts, cb) => cb(gone, "", "ERROR: The process \"11\" not found."));
    await expect(endProcessTrees([11], { execFile })).resolves.toBeUndefined();
  });

  test("any other failure says which process", async () => {
    const denied = Object.assign(new Error("exit 1"), { code: 1 });
    const execFile = jest.fn((cmd, args, opts, cb) => cb(denied, "", "ERROR: Access is denied."));
    await expect(endProcessTrees([11], { execFile })).rejects.toThrow("Could not end process 11: ERROR: Access is denied.");
  });
});
