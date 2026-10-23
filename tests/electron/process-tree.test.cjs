// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * process-tree.cjs against real processes: quitting GAIA must stop a child
 * and everything it started, including processes that ignore SIGTERM, and
 * must not touch GAIA's shared servers or a stranger that reused a pid (#3498).
 */

"use strict";

const path = require("path");
const { spawn } = require("child_process");

const {
  listTimeoutMs,
  killProcessTree,
  listDescendants,
  listProcesses,
  descendantsFromTable,
  isSharedServer,
  parsePosixTable,
  parseWindowsTable,
} = require("../../src/gaia/apps/webui/services/process-tree.cjs");

const FIXTURE = path.join(__dirname, "fixtures", "proc-tree-child.cjs");
const quiet = { log: () => {}, logError: () => {} };
const spawned = [];

jest.setTimeout(30000);

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

async function waitFor(pred, ms = 5000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return pred();
}

/** Start the fixture and resolve once it has reported `expected` pids. */
async function startTree(args, expected) {
  const child = spawn(process.execPath, [FIXTURE, ...args], { stdio: ["pipe", "pipe", "ignore"] });
  spawned.push(child);
  const pids = [];
  child.stdout.on("data", (d) => {
    for (const m of String(d).matchAll(/PID (\d+)/g)) pids.push(Number(m[1]));
  });
  const ok = await waitFor(() => pids.length >= expected, 10000);
  if (!ok) throw new Error(`fixture reported ${pids.length}/${expected} pids`);
  pids.forEach((p) => spawned.push(p));
  return { child, pids };
}

afterAll(() => {
  // Never leave a fixture behind, whatever a failed assertion skipped.
  for (const p of spawned) {
    try {
      if (typeof p === "number") process.kill(p, "SIGKILL");
      else p.kill("SIGKILL");
    } catch {
      // Already gone — the expected case.
    }
  }
});

describe("killProcessTree on real processes", () => {
  test("stops the child and its grandchildren", async () => {
    const { child, pids } = await startTree(["--children=2"], 3);
    const result = await killProcessTree(child, { ...quiet, graceMs: 2000 });
    expect(result.survivors).toEqual([]);
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)))).toBe(true);
  });

  test("SIGKILLs a child and grandchildren that ignore SIGTERM", async () => {
    const { child, pids } = await startTree(["--ignore-term", "--children=1"], 2);
    // Let the SIGTERM handlers install before anything is signalled.
    await new Promise((r) => setTimeout(r, 200));
    const start = Date.now();
    const result = await killProcessTree(child, { ...quiet, graceMs: 800 });
    expect(result.survivors).toEqual([]);
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)))).toBe(true);
    if (process.platform !== "win32") {
      // On Linux/macOS SIGTERM was ignored, so the grace period ran out first.
      expect(Date.now() - start).toBeGreaterThanOrEqual(750);
    }
  });

  test("stops grandchildren left behind after the child itself exited", async () => {
    const { child, pids } = await startTree(["--children=1", "--detach-children"], 2);
    const { descendants } = await listDescendants(child.pid);
    expect(descendants.map((d) => d.pid)).toContain(pids[1]);

    // The child dies on its own; its child is orphaned and re-parented.
    child.kill("SIGKILL");
    await waitFor(() => child.exitCode !== null || child.signalCode !== null);
    expect(pidAlive(pids[1])).toBe(true);

    await killProcessTree(child, { ...quiet, graceMs: 1000, knownDescendants: descendants });
    expect(await waitFor(() => !pidAlive(pids[1]))).toBe(true);
  });

  test("spares a snapshotted pid whose command line changed (pid reuse)", async () => {
    const { child: stranger } = await startTree([], 1);
    const fakeRoot = await startTree([], 1);
    // Claim the stranger was the root's child, under a different command.
    await killProcessTree(fakeRoot.child, {
      ...quiet,
      graceMs: 500,
      knownDescendants: [{ pid: stranger.pid, command: "something-else --not-this" }],
    });
    expect(pidAlive(fakeRoot.child.pid)).toBe(false);
    expect(pidAlive(stranger.pid)).toBe(true);
    stranger.kill("SIGKILL");
  });

  test("still stops the root, loudly, when the process table can't be read", async () => {
    const { child } = await startTree([], 1);
    const errors = [];
    await killProcessTree(child, {
      log: () => {},
      logError: (m) => errors.push(m),
      graceMs: 1000,
      listProcesses: async () => {
        throw new Error("ps: not found");
      },
    });
    expect(await waitFor(() => child.exitCode !== null || child.signalCode !== null)).toBe(true);
    expect(errors.join("\n")).toMatch(/ps: not found.*may keep running/);
  });

  test("is a no-op on a child that already exited", async () => {
    const { child } = await startTree([], 1);
    child.kill("SIGKILL");
    await waitFor(() => child.exitCode !== null || child.signalCode !== null);
    const listProcesses = jest.fn(async () => []);
    const result = await killProcessTree(child, { ...quiet, listProcesses });
    expect(result).toEqual({ killed: [], survivors: [] });
  });

  const posixOnly = process.platform === "win32" ? test.skip : test;

  // On Windows SIGTERM is TerminateProcess, so nothing survives to the SIGKILL
  // pass these two tests are about.
  test("on Windows the SIGKILL pass re-reads nothing and targets only the root", async () => {
    const root = await startTree([], 1);
    const bystander = await startTree([], 1);
    let reads = 0;
    const listProcesses = async () => {
      reads += 1;
      return [
        { pid: root.child.pid, ppid: process.pid, command: "root", started: null },
        { pid: bystander.child.pid, ppid: root.child.pid, command: "tool", started: null },
      ];
    };
    // Pretend SIGTERM changed nothing, as for a process Windows refused to end.
    const realKill = root.child.kill.bind(root.child);
    root.child.kill = (sig) => (sig === "SIGKILL" ? realKill("SIGKILL") : true);
    const killSpy = jest.spyOn(process, "kill");
    try {
      await killProcessTree(root.child, { ...quiet, graceMs: 200, listProcesses, platform: "win32" });
      const sigkills = killSpy.mock.calls.filter(([, sig]) => sig === "SIGKILL").map(([p]) => p);
      expect(reads).toBe(1);
      expect(sigkills).not.toContain(bystander.child.pid);
      expect(await waitFor(() => root.child.exitCode !== null || root.child.signalCode !== null)).toBe(true);
    } finally {
      killSpy.mockRestore();
      bystander.child.kill("SIGKILL");
    }
  });

  posixOnly("a member whose pid runs something else by the SIGKILL pass is spared", async () => {
    const root = await startTree(["--ignore-term"], 1);
    const stranger = await startTree(["--ignore-term"], 1);
    await new Promise((r) => setTimeout(r, 200));
    let reads = 0;
    const listProcesses = async () => {
      reads += 1;
      return [
        { pid: root.child.pid, ppid: process.pid, command: "root", started: null },
        // First read: the stranger's pid is the root's child. Second read: that
        // child exited and the pid now belongs to an unrelated program.
        reads === 1
          ? { pid: stranger.child.pid, ppid: root.child.pid, command: "gaia-tool", started: null }
          : { pid: stranger.child.pid, ppid: 1, command: "other-program", started: null },
      ];
    };
    await killProcessTree(root.child, { ...quiet, graceMs: 300, listProcesses });
    expect(reads).toBe(2);
    expect(await waitFor(() => root.child.signalCode !== null)).toBe(true);
    expect(pidAlive(stranger.child.pid)).toBe(true);
  });

  posixOnly("when the SIGKILL pass can't re-read the table, only the root is killed", async () => {
    const root = await startTree(["--ignore-term"], 1);
    const member = await startTree(["--ignore-term"], 1);
    await new Promise((r) => setTimeout(r, 200));
    let reads = 0;
    const errors = [];
    const listProcesses = async () => {
      reads += 1;
      if (reads > 1) throw new Error("ps timed out");
      return [
        { pid: root.child.pid, ppid: process.pid, command: "root", started: null },
        { pid: member.child.pid, ppid: root.child.pid, command: "gaia-tool", started: null },
      ];
    };
    await killProcessTree(root.child, {
      log: () => {},
      logError: (m) => errors.push(m),
      graceMs: 300,
      listProcesses,
    });
    expect(await waitFor(() => root.child.signalCode !== null)).toBe(true);
    expect(pidAlive(member.child.pid)).toBe(true);
    expect(errors.join("\n")).toMatch(/ps timed out.*only the process itself gets SIGKILL/);
  });

  test("two concurrent kills of the same tree both finish", async () => {
    const { child, pids } = await startTree(["--children=1"], 2);
    const [a, b] = await Promise.all([
      killProcessTree(child, { ...quiet, graceMs: 1000 }),
      killProcessTree(child, { ...quiet, graceMs: 1000 }),
    ]);
    expect(a.survivors).toEqual([]);
    expect(b.survivors).toEqual([]);
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)))).toBe(true);
  });
});

describe("descendantsFromTable", () => {
  const table = [
    { pid: 10, ppid: 1, command: "gaia.exe chat --ui --ui-port 4321" },
    { pid: 11, ppid: 10, command: "python.exe -m gaia chat --ui" },
    { pid: 12, ppid: 11, command: "python.exe -m gaia.daemon" },
    { pid: 13, ppid: 12, command: "LemonadeServer.exe --silent" },
    { pid: 14, ppid: 11, command: "npx some-mcp-server" },
    { pid: 15, ppid: 14, command: "node server.js" },
    { pid: 16, ppid: 1, command: "unrelated" },
    { pid: 17, ppid: 17, command: "self-parented" },
  ];

  test("walks children before grandchildren and skips shared servers with their subtree", () => {
    const { descendants, skipped } = descendantsFromTable(table, 10);
    expect(descendants.map((d) => d.pid)).toEqual([11, 14, 15]);
    expect(skipped.map((d) => d.pid)).toEqual([12]);
  });

  test("on Windows, a process older than its claimed parent is not its child (reused pid)", () => {
    const rows = [
      { pid: 30, ppid: 1, command: "gaia.exe chat --ui", started: 2000n },
      // Started before pid 30 existed: its real parent died and 30 was reused.
      { pid: 31, ppid: 30, command: "C:\\Users\\me\\editor.exe", started: 1000n },
      { pid: 32, ppid: 30, command: "python.exe -m gaia chat --ui", started: 2001n },
      { pid: 33, ppid: 32, command: "node mcp.js", started: 2002n },
    ];
    expect(descendantsFromTable(rows, 30).descendants.map((d) => d.pid)).toEqual([32, 33]);
  });

  test("terminates on a parent-pid cycle", () => {
    const cyclic = [
      { pid: 20, ppid: 21, command: "a" },
      { pid: 21, ppid: 20, command: "b" },
    ];
    expect(descendantsFromTable(cyclic, 20).descendants.map((d) => d.pid)).toEqual([21]);
  });

  test.each([
    ["C:\\venv\\Scripts\\python.exe -m gaia.daemon", true],
    ['"C:\\venv\\Scripts\\gaia.exe" daemon start', true],
    ["/usr/bin/lemond ./ --port 13305", true],
    ["C:\\Program Files\\Lemonade\\LemonadeServer.exe --silent", true],
    ['"C:\\Users\\me\\AppData\\Local\\lemonade_server\\bin\\lemonade-server.exe" serve', true],
    ["lemonade-tray.exe", true],
    ["node mcp.js --lemonade-url http://localhost:13305", false],
    ["C:\\tools\\lemonade-helpers\\mcp.exe --stdio", false],
    ["python -m gaia chat --ui --ui-port 4321", false],
    ["node mcp-server-daemonize.js", false],
    ["", false],
  ])("isSharedServer(%j) is %s", (cmd, expected) => {
    expect(isSharedServer(cmd)).toBe(expected);
  });
});

describe("process table parsing", () => {
  test("parses ps output, including commands with spaces and empty commands", () => {
    const rows = parsePosixTable("    1     0 /sbin/init splash\n  420     1 node /a b/c.js --x\n  421   420\n\n");
    expect(rows).toEqual([
      { pid: 1, ppid: 0, command: "/sbin/init splash", started: null },
      { pid: 420, ppid: 1, command: "node /a b/c.js --x", started: null },
      { pid: 421, ppid: 420, command: "", started: null },
    ]);
  });

  test("parses the PowerShell tab-separated output and ignores noise", () => {
    const rows = parseWindowsTable(
      "4\t0\t\t\r\n812\t4\t134369906458328030\tC:\\x.exe a\tb\r\nWARNING: junk\r\n"
    );
    expect(rows).toEqual([
      { pid: 4, ppid: 0, started: null, command: "" },
      { pid: 812, ppid: 4, started: 134369906458328030n, command: "C:\\x.exe a\tb" },
    ]);
  });
});

describe("listProcesses on a loaded PC", () => {
  test("gives the process table query time to finish before giving up", async () => {
    let seen;
    const execFile = (cmd, args, options, cb) => {
      seen = options;
      cb(null, "");
    };
    await listProcesses({ platform: "win32", execFile });
    // A timeout here leaves the agent's children running, so it must cover a
    // slow PowerShell start on a busy machine, not just an idle one.
    expect(listTimeoutMs("win32")).toBeGreaterThanOrEqual(15000);
    expect(seen.timeout).toBe(listTimeoutMs("win32"));
  });
});
