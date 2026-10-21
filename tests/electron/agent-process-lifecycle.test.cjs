// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * AgentProcessManager against real child processes (#3498): a write to an
 * agent that died must not crash the app, and quitting must stop every agent
 * and everything it started — including agents that ignore the shutdown
 * request and SIGTERM, a second quit, and a quit while agents still start.
 *
 * test_agent_process_manager.js covers the same class with a mocked
 * child_process; here only the binary path is redirected, to a Node fixture.
 */

"use strict";

const path = require("path");

const FIXTURE = path.join(__dirname, "fixtures", "proc-tree-child.cjs");
const mockFixtureArgs = { next: [] };

jest.mock("child_process", () => {
  const actual = jest.requireActual("child_process");
  return {
    ...actual,
    // The manager runs `<agent binary> --stdio`; run the fixture instead.
    spawn: (cmd, args, opts) =>
      args && args[0] === "--stdio"
        ? actual.spawn(process.execPath, [FIXTURE, ...mockFixtureArgs.next, ...args], opts)
        : actual.spawn(cmd, args, opts),
  };
});

const AgentProcessManager = require("../../src/gaia/apps/webui/services/agent-process-manager.cjs");
const { ipcMain, BrowserWindow } = require("electron");

jest.setTimeout(45000);

const managers = [];
const pidsToReap = [];

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

async function waitFor(pred, ms = 8000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return pred();
}

function makeManager(agents = { a: {}, b: {} }) {
  ipcMain._handlers.clear();
  const manager = new AgentProcessManager(new BrowserWindow());
  manager.manifest = {
    manifest_version: 1,
    agents: Object.keys(agents).map((id) => ({ id, name: id, binaries: {} })),
  };
  manager.config = { agents, tray: {} };
  // Any existing file passes the binary check; spawn is redirected above.
  manager._resolveBinaryPath = () => process.execPath;
  manager._logCrash = jest.fn();
  managers.push(manager);
  return manager;
}

/** Start an agent with fixture flags; resolve with every pid in its tree. */
async function startAgent(manager, id, flags, expectedPids = 1) {
  mockFixtureArgs.next = flags;
  await manager.startAgent(id);
  mockFixtureArgs.next = [];
  const entry = manager.processes[id];
  const pids = [];
  entry.process.stderr.on("data", (d) => {
    for (const m of String(d).matchAll(/PID (\d+)/g)) pids.push(Number(m[1]));
  });
  if (!(await waitFor(() => pids.length >= expectedPids))) {
    throw new Error(`agent ${id} reported ${pids.length}/${expectedPids} pids`);
  }
  pidsToReap.push(...pids);
  return { child: entry.process, pids };
}

let uncaught;
const onUncaught = (err) => uncaught.push(err);

beforeEach(() => {
  uncaught = [];
  process.on("uncaughtException", onUncaught);
});

afterEach(async () => {
  process.removeListener("uncaughtException", onUncaught);
  for (const m of managers.splice(0)) {
    for (const entry of Object.values(m.processes)) {
      if (entry.healthTimer) clearInterval(entry.healthTimer);
    }
  }
});

afterAll(() => {
  for (const pid of pidsToReap) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // Already gone — the expected case.
    }
  }
});

describe("a write to an agent that can no longer read", () => {
  async function expectStoppedAndReported(manager, child, crashed) {
    expect(await waitFor(() => child.exitCode !== null || child.signalCode !== null)).toBe(true);
    expect(await waitFor(() => crashed.length === 1)).toBe(true);
    expect(manager.processes.a).toBeUndefined();
    expect(uncaught).toEqual([]);
  }

  function watchCrashes(manager) {
    const crashed = [];
    manager._sendToRenderer = (channel, data) => {
      if (channel === "agent:crashed") crashed.push(data);
    };
    return crashed;
  }

  test("an EPIPE on a live agent's stdin fails the pending call and stops the agent", async () => {
    const manager = makeManager({ a: { restartOnCrash: false } });
    const crashed = watchCrashes(manager);
    const { child } = await startAgent(manager, "a", ["--stdio", "--ignore-shutdown"]);

    const ping = manager.sendJsonRpc("a", "ping-that-never-answers", {}, 30000);
    // What the OS reports when the reading end is gone. A bare 'error' with
    // no listener would be thrown as an uncaught exception.
    child.stdin.emit("error", Object.assign(new Error("write EPIPE"), { code: "EPIPE", syscall: "write" }));

    await expect(ping).rejects.toThrow(/stopped reading its input \(EPIPE\)/);
    await expect(manager.sendJsonRpc("a", "ping", {})).rejects.toThrow(/pipe broke earlier/);
    await expectStoppedAndReported(manager, child, crashed);
  });

  (process.platform === "win32" ? test.skip : test)(
    "a real EPIPE from an agent that closed its stdin is handled the same way",
    async () => {
      const manager = makeManager({ a: { restartOnCrash: false } });
      const crashed = watchCrashes(manager);
      const { child } = await startAgent(manager, "a", ["--close-stdin"]);
      await new Promise((r) => setTimeout(r, 300));

      // The 30 s health ping: write() returns, the EPIPE arrives afterwards.
      const ping = manager.sendJsonRpc("a", "ping", {}, 10000);
      await expect(ping).rejects.toThrow(/stopped reading its input|process exited/);
      await expectStoppedAndReported(manager, child, crashed);
    }
  );

  test("a ping racing the agent's death neither crashes nor waits out its timeout", async () => {
    const manager = makeManager({ a: { restartOnCrash: false } });
    const { child } = await startAgent(manager, "a", ["--stdio"]);

    child.kill("SIGKILL");
    const started = Date.now();
    // Keep writing until the pipe breaks or the exit is seen.
    const results = [];
    for (let i = 0; i < 20 && manager.processes.a; i++) {
      results.push(manager.sendJsonRpc("a", "ping", {}, 10000).then(() => "ok", (e) => e.message));
      await new Promise((r) => setTimeout(r, 5));
    }
    const settled = await Promise.all(results);
    expect(Date.now() - started).toBeLessThan(5000);
    expect(settled.every((m) => m === "ok" || /stopped reading|process exited|Cannot write/.test(m))).toBe(true);
    expect(await waitFor(() => manager.processes.a === undefined)).toBe(true);

    // Writing after the agent is gone is a clear error, not a crash.
    await expect(manager.sendJsonRpc("a", "ping", {})).rejects.toThrow(/not running/);
    expect(uncaught).toEqual([]);
  });
});

describe("stopping agents", () => {
  test("an agent that ignores shutdown and SIGTERM is killed with its grandchildren", async () => {
    const manager = makeManager();
    const { pids } = await startAgent(
      manager,
      "a",
      ["--ignore-shutdown", "--ignore-term", "--children=2", "--detach-children"],
      3
    );
    await new Promise((r) => setTimeout(r, 200));

    const start = Date.now();
    await manager.stopAgent("a");
    // 5 s shutdown wait + 2 s SIGTERM grace + the SIGKILL wait, with slack.
    expect(Date.now() - start).toBeLessThan(15000);
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)))).toBe(true);
    expect(manager.processes.a).toBeUndefined();
  });

  test("grandchildren are stopped even when the agent exits cleanly on shutdown", async () => {
    const manager = makeManager();
    const { pids } = await startAgent(manager, "a", ["--children=1", "--detach-children"], 2);

    await manager.stopAgent("a");
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)))).toBe(true);
  });

  test("a second quit while the first is stopping agents waits for the same stop", async () => {
    const manager = makeManager();
    const a = await startAgent(manager, "a", ["--stdio", "--ignore-shutdown"]);
    const b = await startAgent(manager, "b", ["--stdio"]);

    let firstDone = false;
    const first = manager.stopAll().then(() => {
      firstDone = true;
    });
    await manager.stopAll();
    // The second quit returned only after the first had stopped everything.
    expect(firstDone).toBe(true);
    await first;
    expect([...a.pids, ...b.pids].filter(pidAlive)).toEqual([]);
    expect(Object.keys(manager.processes)).toEqual([]);
  });

  test("quitting while agents are still auto-starting starts no more of them", async () => {
    const manager = makeManager({
      a: { autoStart: true },
      b: { autoStart: true },
      c: { autoStart: true },
    });
    mockFixtureArgs.next = ["--stdio"];
    const starting = manager.startAllEnabled();
    // Quit as soon as the first agent is up.
    await waitFor(() => Object.keys(manager.processes).length >= 1);
    const first = Object.values(manager.processes).map((e) => e.process);
    await manager.stopAll();
    await starting;
    mockFixtureArgs.next = [];

    expect(Object.keys(manager.processes)).toEqual([]);
    expect(first.every((c) => c.exitCode !== null || c.signalCode !== null)).toBe(true);
    await expect(manager.startAgent("c")).rejects.toThrow(/GAIA is quitting/);
  });

  test("a crash restart due after the quit does not start the agent again", async () => {
    jest.useFakeTimers();
    try {
      const manager = makeManager({ a: { restartOnCrash: true } });
      const start = jest.spyOn(manager, "startAgent");
      manager._attemptCrashRestart("a");
      manager._shuttingDown = true;
      await jest.advanceTimersByTimeAsync(5000);
      expect(start).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });
});
