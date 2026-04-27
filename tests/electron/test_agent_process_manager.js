// Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for AgentProcessManager
 *
 * Validates the full lifecycle of agent subprocess management:
 *   - Process spawning and teardown
 *   - JSON-RPC 2.0 over stdio communication
 *   - Health checks, crash recovery, rate-limiting
 *   - IPC handler registration
 *   - Config/manifest loading
 *   - Edge cases (concurrent stops, destroyed windows, buffer overflow)
 */

const { EventEmitter } = require("events");
const path = require("path");

// ── Mock child_process ──────────────────────────────────────────────────────

// Jest mock factory requires variables prefixed with "mock" for out-of-scope access.
// We use a holder object so the spawn mock can read the current value at call time.
const mockSpawnHolder = { returnValue: null };

/** Create a mock ChildProcess that behaves like a real spawned process. */
function mockCreateChildProcess() {
  const stdin = { write: jest.fn(), destroyed: false };
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const proc = new EventEmitter();

  proc.stdin = stdin;
  proc.stdout = stdout;
  proc.stderr = stderr;
  proc.pid = Math.floor(Math.random() * 90000) + 10000;
  proc.exitCode = null;
  proc.kill = jest.fn(() => {
    proc.exitCode = null; // killed
  });

  return proc;
}

jest.mock("child_process", () => ({
  spawn: jest.fn(() => {
    return mockSpawnHolder.returnValue || mockCreateChildProcess();
  }),
}));

// ── Mock fs ─────────────────────────────────────────────────────────────────

const mockFsImpl = {
  existsSync: jest.fn(() => false),
  readFileSync: jest.fn(() => "{}"),
  writeFileSync: jest.fn(),
  mkdirSync: jest.fn(),
};
jest.mock("fs", () => mockFsImpl);

// ── Mock os ─────────────────────────────────────────────────────────────────

jest.mock("os", () => ({
  homedir: jest.fn(() => "/mock/home"),
  platform: "win32",
  type: jest.fn(() => "Windows_NT"),
  release: jest.fn(() => "10.0.0"),
  arch: jest.fn(() => "x64"),
  EOL: "\n",
}));

// ── Electron mock is handled by moduleNameMapper in jest config ─────────────

const { BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");

// ── Load the module under test ──────────────────────────────────────────────

const AgentProcessManager = require("../../src/gaia/apps/webui/services/agent-process-manager");

// ── Test helpers ────────────────────────────────────────────────────────────

const SAMPLE_MANIFEST = {
  manifest_version: 1,
  agents: [
    {
      id: "test-agent",
      name: "Test Agent",
      description: "A test agent",
      binaries: { win32: "test-agent.exe", darwin: "test-agent", linux: "test-agent" },
    },
    {
      id: "second-agent",
      name: "Second Agent",
      description: "Another agent",
      binaries: { win32: "second.exe", darwin: "second", linux: "second" },
    },
  ],
};

const SAMPLE_CONFIG = {
  agents: {
    "test-agent": { autoStart: true, restartOnCrash: true },
    "second-agent": { autoStart: false, restartOnCrash: false },
  },
  tray: {},
};

/** Set up fs mocks so manifest and config are loadable. */
function setupFsMocks({ manifest, config } = {}) {
  const manifestJson = JSON.stringify(manifest || SAMPLE_MANIFEST);
  const configJson = JSON.stringify(config || SAMPLE_CONFIG);

  mockFsImpl.existsSync.mockImplementation((p) => {
    if (typeof p === "string") {
      if (p.includes("agent-manifest.json")) return true;
      if (p.includes("tray-config.json")) return true;
      // Agent binary exists
      if (p.includes("test-agent.exe") || p.includes("test-agent")) return true;
      if (p.includes("second.exe") || p.includes("second")) return true;
      if (p.includes("crash-log.json")) return false;
      if (p.includes(".gaia")) return true;
    }
    return false;
  });

  mockFsImpl.readFileSync.mockImplementation((p) => {
    if (typeof p === "string") {
      if (p.includes("agent-manifest.json")) return manifestJson;
      if (p.includes("tray-config.json")) return configJson;
      if (p.includes("crash-log.json")) return "[]";
    }
    return "{}";
  });
}

/** Create a fresh manager. Resets ipcMain handlers and spawn mock. */
function createManager(options = {}) {
  // Clear any previous ipcMain handlers
  ipcMain._handlers.clear();

  const mainWindow = options.mainWindow || new BrowserWindow();
  setupFsMocks(options);

  // Reset spawn to return fresh mock processes
  mockSpawnHolder.returnValue = null;
  spawn.mockClear();

  const manager = new AgentProcessManager(mainWindow);
  _activeManagers.push(manager);
  return { manager, mainWindow };
}

/**
 * Utility: simulate an agent being started and return references to its mock process.
 * Sets up spawn to return a controllable mock child process.
 */
async function startMockAgent(manager, agentId = "test-agent") {
  const mockChild = mockCreateChildProcess();
  mockSpawnHolder.returnValue = mockChild;

  const result = await manager.startAgent(agentId);

  // Clear the holder so subsequent spawns get fresh processes
  mockSpawnHolder.returnValue = null;

  return { mockChild, result };
}

// Track all managers created during tests so we can clean up health-check intervals
let _activeManagers = [];
const _origCreateManager = null; // placeholder, we wrap createManager below

// ── Tests ───────────────────────────────────────────────────────────────────

describe("AgentProcessManager", () => {
  beforeEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();

    // Reset holder and mock state while preserving module-level mock wiring
    mockSpawnHolder.returnValue = null;
    mockFsImpl.existsSync.mockReset();
    mockFsImpl.readFileSync.mockReset();
    mockFsImpl.writeFileSync.mockReset();
    mockFsImpl.mkdirSync.mockReset();

    // Re-establish the spawn default implementation (mockReset clears it)
    spawn.mockReset();
    spawn.mockImplementation(() => {
      return mockSpawnHolder.returnValue || mockCreateChildProcess();
    });

    _activeManagers = [];
  });

  afterEach(() => {
    // Clean up real setInterval handles (health-check timers) from any started agents
    for (const mgr of _activeManagers) {
      if (mgr.processes) {
        for (const [, entry] of Object.entries(mgr.processes)) {
          if (entry && entry.healthTimer) {
            clearInterval(entry.healthTimer);
            entry.healthTimer = null;
          }
        }
      }
    }
    _activeManagers = [];
    jest.clearAllTimers();
    jest.useRealTimers();
  });

  // ── 1. Initialization ──────────────────────────────────────────────────

  describe("Initialization", () => {
    it("should extend EventEmitter", () => {
      const { manager } = createManager();
      expect(manager).toBeInstanceOf(EventEmitter);
    });

    it("should initialize empty processes map", () => {
      const { manager } = createManager();
      expect(manager.processes).toEqual({});
    });

    it("should load manifest from disk on construction", () => {
      const { manager } = createManager();
      expect(manager.manifest).toBeDefined();
      expect(manager.manifest.agents).toHaveLength(2);
      expect(manager.manifest.agents[0].id).toBe("test-agent");
    });

    it("should load config from disk on construction", () => {
      const { manager } = createManager();
      expect(manager.config).toBeDefined();
      expect(manager.config.agents["test-agent"].autoStart).toBe(true);
    });

    it("should register all IPC handlers on construction", () => {
      const { manager } = createManager();
      const expectedChannels = [
        "agent:start",
        "agent:stop",
        "agent:restart",
        "agent:status",
        "agent:status-all",
        "agent:send-rpc",
        "agent:get-manifest",
        "agent:install",
        "agent:uninstall",
      ];

      for (const channel of expectedChannels) {
        expect(ipcMain._handlers.has(channel)).toBe(true);
      }
    });

    it("should store mainWindow reference", () => {
      const mainWindow = new BrowserWindow();
      const { manager } = createManager({ mainWindow });
      expect(manager.mainWindow).toBe(mainWindow);
    });

    it("should initialize empty _crashTimes", () => {
      const { manager } = createManager();
      expect(manager._crashTimes).toEqual({});
    });

    it("should fall back to empty manifest if no file exists", () => {
      ipcMain._handlers.clear();
      mockFsImpl.existsSync.mockReturnValue(false);
      mockFsImpl.readFileSync.mockImplementation(() => {
        throw new Error("ENOENT");
      });

      const mainWindow = new BrowserWindow();
      const manager = new AgentProcessManager(mainWindow);

      expect(manager.manifest).toEqual({ manifest_version: 1, agents: [] });
    });

    it("should fall back to default config if file does not exist", () => {
      ipcMain._handlers.clear();
      mockFsImpl.existsSync.mockReturnValue(false);

      const mainWindow = new BrowserWindow();
      const manager = new AgentProcessManager(mainWindow);

      expect(manager.config).toEqual({ agents: {}, tray: {} });
    });
  });

  // ── 2. Agent Lifecycle ────────────────────────────────────────────────

  describe("Agent Lifecycle", () => {
    describe("startAgent", () => {
      it("should spawn a child process with correct arguments", async () => {
        const { manager } = createManager();
        await startMockAgent(manager);

        expect(spawn).toHaveBeenCalledTimes(1);
        const spawnCall = spawn.mock.calls[0];
        expect(spawnCall[1]).toEqual(["--stdio"]);
        expect(spawnCall[2].stdio).toEqual(["pipe", "pipe", "pipe"]);
        expect(spawnCall[2].windowsHide).toBe(true);
      });

      it("should return the pid of the spawned process", async () => {
        const { manager } = createManager();
        const { result, mockChild } = await startMockAgent(manager);

        expect(result).toEqual({ pid: mockChild.pid });
      });

      it("should store the process entry with correct initial state", async () => {
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        const entry = manager.processes["test-agent"];
        expect(entry).toBeDefined();
        expect(entry.process).toBe(mockChild);
        expect(entry.stderrBuffer).toEqual([]);
        expect(entry.stdoutBuffer).toBe("");
        expect(entry.rpcIdCounter).toBe(1);
        expect(entry.pendingRpc).toEqual({});
        expect(entry.stopping).toBe(false);
        expect(entry.healthTimer).not.toBeNull();
        expect(typeof entry.startedAt).toBe("number");
      });

      it("should emit running status change on start", async () => {
        const { manager, mainWindow } = createManager();
        const statusEvents = [];
        manager.on("status-change", (e) => statusEvents.push(e));

        await startMockAgent(manager);

        expect(statusEvents).toHaveLength(1);
        expect(statusEvents[0].agentId).toBe("test-agent");
        expect(statusEvents[0].status).toBe("running");
      });

      it("should return existing pid if agent is already running", async () => {
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        // Start again — should not spawn a new process
        const result2 = await manager.startAgent("test-agent");
        expect(result2).toEqual({ pid: mockChild.pid });
        expect(spawn).toHaveBeenCalledTimes(1); // only the first call
      });

      it("should throw if agent not found in manifest", async () => {
        const { manager } = createManager();

        await expect(manager.startAgent("nonexistent-agent")).rejects.toThrow(
          'Agent "nonexistent-agent" not found in manifest'
        );
      });

      it("should throw if agent binary does not exist", async () => {
        const { manager } = createManager();

        // Override existsSync to return false for binary path
        mockFsImpl.existsSync.mockImplementation((p) => {
          if (typeof p === "string") {
            if (p.includes("agent-manifest.json")) return true;
            if (p.includes("tray-config.json")) return true;
          }
          return false; // binary not found
        });

        // Need to re-create since manifest was loaded in constructor
        // Instead, directly ensure the binary check fails
        const origExistsSync = mockFsImpl.existsSync;
        mockFsImpl.existsSync.mockImplementation((p) => {
          if (typeof p === "string" && (p.includes("test-agent.exe") || p.endsWith("test-agent"))) {
            return false;
          }
          return origExistsSync(p);
        });

        await expect(manager.startAgent("test-agent")).rejects.toThrow(
          /Agent binary not found/
        );
      });

      it("should set up stdout, stderr, and exit listeners on the child process", async () => {
        const { manager } = createManager();
        const mockChild = mockCreateChildProcess();
        const stdoutOnSpy = jest.spyOn(mockChild.stdout, "on");
        const stderrOnSpy = jest.spyOn(mockChild.stderr, "on");
        const childOnSpy = jest.spyOn(mockChild, "on");

        mockSpawnHolder.returnValue = mockChild;

        await manager.startAgent("test-agent");

        expect(stdoutOnSpy).toHaveBeenCalledWith("data", expect.any(Function));
        expect(stderrOnSpy).toHaveBeenCalledWith("data", expect.any(Function));
        expect(childOnSpy).toHaveBeenCalledWith("error", expect.any(Function));
        expect(childOnSpy).toHaveBeenCalledWith("exit", expect.any(Function));
      });
    });

    describe("stopAgent", () => {
      it("should send JSON-RPC shutdown request via stdin", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        // Start stop, but don't await yet — we need to simulate the exit
        const stopPromise = manager.stopAgent("test-agent");

        // Simulate the process exiting after receiving shutdown
        mockChild.exitCode = 0;
        mockChild.emit("exit", 0, null);

        await stopPromise;

        // Verify shutdown was sent
        expect(mockChild.stdin.write).toHaveBeenCalled();
        const written = mockChild.stdin.write.mock.calls[0][0];
        const parsed = JSON.parse(written.trim());
        expect(parsed.jsonrpc).toBe("2.0");
        expect(parsed.method).toBe("shutdown");

        jest.useRealTimers();
      });

      it("should set stopping flag to true", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        const stopPromise = manager.stopAgent("test-agent");

        // Check the flag is set before process exits
        // (the entry may already be cleaned up after exit, so check the flag on the entry we captured)
        // Since we haven't triggered exit yet, it should be set
        const entry = manager.processes["test-agent"];
        expect(entry.stopping).toBe(true);

        // Let it finish
        mockChild.exitCode = 0;
        mockChild.emit("exit", 0, null);
        await stopPromise;

        jest.useRealTimers();
      });

      it("should force kill if process does not exit within timeout", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        const stopPromise = manager.stopAgent("test-agent");

        // Advance past the shutdown timeout (5000ms) without the process exiting
        jest.advanceTimersByTime(5001);

        await stopPromise;

        expect(mockChild.kill).toHaveBeenCalled();

        jest.useRealTimers();
      });

      it("should do nothing if agent is not running", async () => {
        const { manager } = createManager();
        // Should not throw
        await manager.stopAgent("nonexistent-agent");
      });

      it("should skip duplicate concurrent stop calls", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        // First stop call
        const stop1 = manager.stopAgent("test-agent");

        // Second concurrent stop call — should return immediately
        const stop2 = manager.stopAgent("test-agent");

        // Let process exit
        mockChild.exitCode = 0;
        mockChild.emit("exit", 0, null);

        await stop1;
        await stop2;

        // shutdown should only have been sent once
        expect(mockChild.stdin.write).toHaveBeenCalledTimes(1);

        jest.useRealTimers();
      });

      it("should clear health check timer on stop", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        const entry = manager.processes["test-agent"];
        expect(entry.healthTimer).not.toBeNull();

        const stopPromise = manager.stopAgent("test-agent");
        mockChild.exitCode = 0;
        mockChild.emit("exit", 0, null);
        await stopPromise;

        // Process entry is deleted after cleanup
        expect(manager.processes["test-agent"]).toBeUndefined();

        jest.useRealTimers();
      });

      it("should emit stopped status change", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild } = await startMockAgent(manager);

        const statusEvents = [];
        manager.on("status-change", (e) => statusEvents.push(e));

        const stopPromise = manager.stopAgent("test-agent");
        mockChild.exitCode = 0;
        mockChild.emit("exit", 0, null);
        await stopPromise;

        const stoppedEvent = statusEvents.find((e) => e.status === "stopped");
        expect(stoppedEvent).toBeDefined();
        expect(stoppedEvent.agentId).toBe("test-agent");

        jest.useRealTimers();
      });
    });

    describe("restartAgent", () => {
      it("should stop and then start the agent", async () => {
        jest.useFakeTimers();
        const { manager } = createManager();
        const { mockChild: firstChild } = await startMockAgent(manager);

        // Set up a new mock child for the restart
        const secondChild = mockCreateChildProcess();
        mockSpawnHolder.returnValue = secondChild;

        const restartPromise = manager.restartAgent("test-agent");

        // Let the old process exit
        firstChild.exitCode = 0;
        firstChild.emit("exit", 0, null);

        const result = await restartPromise;

        expect(result).toEqual({ pid: secondChild.pid });
        expect(manager.processes["test-agent"]).toBeDefined();
        expect(manager.processes["test-agent"].process).toBe(secondChild);

        jest.useRealTimers();
      });
    });
  });

  // ── 3. stdout handling ────────────────────────────────────────────────

  describe("stdout handling", () => {
    it("should parse newline-delimited JSON-RPC messages", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const msg = { jsonrpc: "2.0", method: "notification/send", params: { text: "hello" } };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(msg) + "\n"));

      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "agent:stdout",
        expect.objectContaining({
          agentId: "test-agent",
          message: msg,
        })
      );
    });

    it("should resolve pending RPC on matching response id", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Set up a pending RPC
      const rpcPromise = manager.sendJsonRpc("test-agent", "test-method", { key: "val" });

      // Get the id from what was written to stdin
      const written = mockChild.stdin.write.mock.calls[0][0];
      const sentMsg = JSON.parse(written.trim());

      // Send back a matching response
      const response = { jsonrpc: "2.0", id: sentMsg.id, result: { success: true } };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));

      const result = await rpcPromise;
      expect(result).toEqual({ success: true });
    });

    it("should reject pending RPC on error response", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const rpcPromise = manager.sendJsonRpc("test-agent", "bad-method", {});

      const written = mockChild.stdin.write.mock.calls[0][0];
      const sentMsg = JSON.parse(written.trim());

      const errorResponse = {
        jsonrpc: "2.0",
        id: sentMsg.id,
        error: { code: -32601, message: "Method not found" },
      };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(errorResponse) + "\n"));

      await expect(rpcPromise).rejects.toThrow("Method not found");
    });

    it("should handle multiple messages in a single data chunk", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const msg1 = { jsonrpc: "2.0", method: "notif1", params: {} };
      const msg2 = { jsonrpc: "2.0", method: "notif2", params: {} };
      const combined = JSON.stringify(msg1) + "\n" + JSON.stringify(msg2) + "\n";

      mockChild.stdout.emit("data", Buffer.from(combined));

      const calls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stdout"
      );
      // One call from startAgent (status-change), then two stdout calls
      expect(calls).toHaveLength(2);
    });

    it("should buffer partial messages across data events", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const fullMsg = JSON.stringify({ jsonrpc: "2.0", method: "partial-test", params: {} });
      const half1 = fullMsg.slice(0, Math.floor(fullMsg.length / 2));
      const half2 = fullMsg.slice(Math.floor(fullMsg.length / 2)) + "\n";

      // Send first half — should not emit
      mockChild.stdout.emit("data", Buffer.from(half1));
      const callsBefore = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stdout"
      );
      expect(callsBefore).toHaveLength(0);

      // Send second half — now it should emit
      mockChild.stdout.emit("data", Buffer.from(half2));
      const callsAfter = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stdout"
      );
      expect(callsAfter).toHaveLength(1);
      expect(callsAfter[0][1].message.method).toBe("partial-test");
    });

    it("should discard buffer on overflow (> 1MB)", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Send a huge chunk without any newlines to trigger overflow
      const hugeData = "x".repeat(1024 * 1024 + 1);
      mockChild.stdout.emit("data", Buffer.from(hugeData));

      // Buffer should have been cleared
      const entry = manager.processes["test-agent"];
      expect(entry.stdoutBuffer).toBe("");
    });

    it("should silently skip non-JSON lines", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Send non-JSON output
      mockChild.stdout.emit("data", Buffer.from("this is not json\n"));

      // Should not forward non-JSON to renderer as stdout message
      const stdoutCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stdout"
      );
      expect(stdoutCalls).toHaveLength(0);
    });

    it("should skip empty lines", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.stdout.emit("data", Buffer.from("\n\n\n"));

      const stdoutCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stdout"
      );
      expect(stdoutCalls).toHaveLength(0);
    });

    it("should emit agent-notification event for notification/send method", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const notifications = [];
      manager.on("agent-notification", (agentId, params) => {
        notifications.push({ agentId, params });
      });

      const msg = {
        jsonrpc: "2.0",
        method: "notification/send",
        params: { type: "info", message: "hello" },
      };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(msg) + "\n"));

      expect(notifications).toHaveLength(1);
      expect(notifications[0].agentId).toBe("test-agent");
      expect(notifications[0].params.message).toBe("hello");
    });

    it("should not crash if process entry was deleted before data arrives", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Clean up health timer before deleting the entry
      const entry = manager.processes["test-agent"];
      if (entry && entry.healthTimer) {
        clearInterval(entry.healthTimer);
      }
      delete manager.processes["test-agent"];

      // Should not throw
      mockChild.stdout.emit("data", Buffer.from('{"jsonrpc":"2.0"}\n'));
    });
  });

  // ── 4. stderr handling ────────────────────────────────────────────────

  describe("stderr handling", () => {
    it("should add lines to the circular buffer", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.stderr.emit("data", Buffer.from("line one\nline two\n"));

      const entry = manager.processes["test-agent"];
      expect(entry.stderrBuffer).toContain("line one");
      expect(entry.stderrBuffer).toContain("line two");
    });

    it("should forward stderr lines to renderer", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.stderr.emit("data", Buffer.from("error message\n"));

      const stderrCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:stderr"
      );
      expect(stderrCalls).toHaveLength(1);
      expect(stderrCalls[0][1].agentId).toBe("test-agent");
      expect(stderrCalls[0][1].line).toBe("error message");
    });

    it("should enforce circular buffer limit of 10000 lines", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const entry = manager.processes["test-agent"];

      // Pre-fill the buffer to near capacity
      for (let i = 0; i < 10000; i++) {
        entry.stderrBuffer.push(`line-${i}`);
      }
      expect(entry.stderrBuffer).toHaveLength(10000);

      // Add one more via stderr event
      mockChild.stderr.emit("data", Buffer.from("overflow-line\n"));

      expect(entry.stderrBuffer).toHaveLength(10000);
      expect(entry.stderrBuffer[entry.stderrBuffer.length - 1]).toBe("overflow-line");
      // First original line should have been shifted out
      expect(entry.stderrBuffer[0]).toBe("line-1");
    });

    it("should skip empty lines in stderr", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.stderr.emit("data", Buffer.from("\n\n"));

      const entry = manager.processes["test-agent"];
      expect(entry.stderrBuffer).toHaveLength(0);
    });

    it("should not crash if process entry was deleted before stderr data", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Clean up health timer before deleting the entry
      const entry = manager.processes["test-agent"];
      if (entry && entry.healthTimer) {
        clearInterval(entry.healthTimer);
      }
      delete manager.processes["test-agent"];

      // Should not throw
      mockChild.stderr.emit("data", Buffer.from("late data\n"));
    });
  });

  // ── 5. Process exit & crash recovery ──────────────────────────────────

  describe("Process exit handling", () => {
    it("should skip crash handler when stopping flag is true (intentional stop)", async () => {
      jest.useFakeTimers();
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Clear sends so far
      mainWindow.webContents.send.mockClear();

      const stopPromise = manager.stopAgent("test-agent");

      // Simulate exit during intentional stop
      mockChild.exitCode = 0;
      mockChild.emit("exit", 0, null);

      await stopPromise;

      // Should NOT have sent agent:crashed since it was intentional
      const crashCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:crashed"
      );
      expect(crashCalls).toHaveLength(0);

      jest.useRealTimers();
    });

    it("should send agent:crashed to renderer on unexpected exit", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mainWindow.webContents.send.mockClear();

      // Unexpected crash
      mockChild.emit("exit", 1, null);

      const crashCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:crashed"
      );
      expect(crashCalls).toHaveLength(1);
      expect(crashCalls[0][1].agentId).toBe("test-agent");
      expect(crashCalls[0][1].exitCode).toBe(1);
    });

    it("should log crash for non-zero exit code", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.emit("exit", 42, null);

      expect(mockFsImpl.writeFileSync).toHaveBeenCalled();
      const writeCall = mockFsImpl.writeFileSync.mock.calls[0];
      expect(writeCall[0]).toContain("crash-log.json");
      const logData = JSON.parse(writeCall[1]);
      expect(logData[0].agentId).toBe("test-agent");
      expect(logData[0].exitCode).toBe(42);
    });

    it("should NOT log crash for exit code 0", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.emit("exit", 0, null);

      expect(mockFsImpl.writeFileSync).not.toHaveBeenCalled();
    });

    it("should clean up process entry after unexpected exit", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.emit("exit", 1, null);

      expect(manager.processes["test-agent"]).toBeUndefined();
    });

    it("should emit stopped status on unexpected exit", async () => {
      const { manager } = createManager();
      await startMockAgent(manager);

      const statusEvents = [];
      manager.on("status-change", (e) => statusEvents.push(e));

      manager.processes["test-agent"].process.emit("exit", 1, null);

      const stoppedEvent = statusEvents.find((e) => e.status === "stopped");
      expect(stoppedEvent).toBeDefined();
    });

    it("should attempt crash restart when restartOnCrash is enabled", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      await startMockAgent(manager);

      const firstChild = manager.processes["test-agent"].process;

      // Prepare a new child for the restart
      const secondChild = mockCreateChildProcess();
      mockSpawnHolder.returnValue = secondChild;

      // Trigger unexpected crash (non-zero code)
      firstChild.emit("exit", 1, null);

      // Crash restart has a delay
      jest.advanceTimersByTime(2001);

      // Need to flush microtasks for the async startAgent
      await Promise.resolve();
      await Promise.resolve();

      // Agent should have been restarted
      expect(spawn).toHaveBeenCalledTimes(2); // original + restart

      jest.useRealTimers();
    });

    it("should NOT attempt crash restart when restartOnCrash is false", async () => {
      jest.useFakeTimers();
      const { manager } = createManager({
        config: {
          agents: {
            "test-agent": { autoStart: false, restartOnCrash: false },
          },
          tray: {},
        },
      });
      await startMockAgent(manager);

      manager.processes["test-agent"].process.emit("exit", 1, null);

      jest.advanceTimersByTime(5000);
      await Promise.resolve();

      // Only the initial spawn
      expect(spawn).toHaveBeenCalledTimes(1);

      jest.useRealTimers();
    });

    it("should rate-limit crash restarts (max 3 within 60s)", async () => {
      const { manager } = createManager();

      const crashLimitEvents = [];
      manager.on("agent-crash-limit", (agentId, count) => {
        crashLimitEvents.push({ agentId, count });
      });

      // Pre-fill crash times to simulate 3 recent crashes within the window
      const now = Date.now();
      manager._crashTimes["test-agent"] = [now - 5000, now - 3000, now - 1000];

      // 4th crash attempt should be rate-limited
      manager._attemptCrashRestart("test-agent");

      expect(crashLimitEvents).toHaveLength(1);
      expect(crashLimitEvents[0].agentId).toBe("test-agent");
      expect(crashLimitEvents[0].count).toBe(4);
    });

    it("should emit error status on spawn error", async () => {
      const { manager, mainWindow } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mainWindow.webContents.send.mockClear();

      mockChild.emit("error", new Error("ENOENT"));

      const statusCalls = mainWindow.webContents.send.mock.calls.filter(
        (c) => c[0] === "agent:status-change" && c[1].status === "error"
      );
      expect(statusCalls).toHaveLength(1);
      expect(statusCalls[0][1].detail).toBe("ENOENT");
    });
  });

  // ── 6. JSON-RPC ───────────────────────────────────────────────────────

  describe("JSON-RPC", () => {
    it("should send well-formed JSON-RPC request with incrementing ids", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // We need to send and not await (since there won't be a response)
      // Use _sendJsonRpcRaw directly for this test
      manager._sendJsonRpcRaw("test-agent", "my-method", { foo: "bar" }, "custom-id");

      const written = mockChild.stdin.write.mock.calls[0][0];
      const parsed = JSON.parse(written.trim());
      expect(parsed.jsonrpc).toBe("2.0");
      expect(parsed.method).toBe("my-method");
      expect(parsed.params).toEqual({ foo: "bar" });
      expect(parsed.id).toBe("custom-id");
    });

    it("should send notification (no id) when id is omitted", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      manager._sendJsonRpcRaw("test-agent", "some-notif", {});

      expect(mockChild.stdin.write).toHaveBeenCalled();
      const calls = mockChild.stdin.write.mock.calls;
      const lastCall = calls[calls.length - 1][0];
      const parsed = JSON.parse(lastCall.trim());
      expect(parsed.id).toBeUndefined();
      expect(parsed.method).toBe("some-notif");
    });

    it("should throw when writing to stdin of non-existent agent", () => {
      const { manager } = createManager();

      expect(() => {
        manager._sendJsonRpcRaw("nonexistent", "test", {});
      }).toThrow('Cannot write to stdin of agent "nonexistent"');
    });

    it("should throw when stdin is destroyed", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Directly modify the entry's process stdin
      const entry = manager.processes["test-agent"];
      entry.process.stdin.destroyed = true;

      expect(() => {
        manager._sendJsonRpcRaw("test-agent", "test", {});
      }).toThrow('Cannot write to stdin of agent "test-agent"');
    });

    it("should reject sendJsonRpc when agent is not running", async () => {
      const { manager } = createManager();

      await expect(
        manager.sendJsonRpc("not-running", "method", {})
      ).rejects.toThrow('Agent "not-running" is not running');
    });

    it("should reject sendJsonRpc on timeout", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      await startMockAgent(manager);

      const rpcPromise = manager.sendJsonRpc("test-agent", "slow-method", {}, 5000);

      // Advance timer past the timeout
      jest.advanceTimersByTime(5001);

      await expect(rpcPromise).rejects.toThrow(/JSON-RPC timeout.*slow-method.*5000ms/);

      jest.useRealTimers();
    });

    it("should clean up pending RPC entry after timeout", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      await startMockAgent(manager);

      const rpcPromise = manager.sendJsonRpc("test-agent", "timeout-method", {}, 1000);

      jest.advanceTimersByTime(1001);

      try {
        await rpcPromise;
      } catch {
        // Expected timeout error
      }

      const entry = manager.processes["test-agent"];
      expect(Object.keys(entry.pendingRpc)).toHaveLength(0);

      jest.useRealTimers();
    });

    it("should use incrementing RPC ids", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Send two RPCs (without awaiting since we're not sending responses)
      manager.sendJsonRpc("test-agent", "method1", {}).catch(() => {});
      manager.sendJsonRpc("test-agent", "method2", {}).catch(() => {});

      const call1 = JSON.parse(mockChild.stdin.write.mock.calls[0][0].trim());
      const call2 = JSON.parse(mockChild.stdin.write.mock.calls[1][0].trim());

      expect(call1.id).toBe("rpc-1");
      expect(call2.id).toBe("rpc-2");
    });

    it("should append newline to JSON-RPC payload", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      manager._sendJsonRpcRaw("test-agent", "test", {}, "id-1");

      const written = mockChild.stdin.write.mock.calls[0][0];
      expect(written.endsWith("\n")).toBe(true);
    });
  });

  // ── 7. Health check ───────────────────────────────────────────────────

  describe("Health check", () => {
    it("should send ping RPC as health check", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Call health check directly
      const healthPromise = manager._healthCheck("test-agent");

      // Respond to the ping
      const written = mockChild.stdin.write.mock.calls[0][0];
      const sentMsg = JSON.parse(written.trim());
      const response = { jsonrpc: "2.0", id: sentMsg.id, result: { status: "ok" } };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));

      await healthPromise;
      // Should not throw
    });

    it("should store memory usage from ping response", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const healthPromise = manager._healthCheck("test-agent");

      const written = mockChild.stdin.write.mock.calls[0][0];
      const sentMsg = JSON.parse(written.trim());
      const response = {
        jsonrpc: "2.0",
        id: sentMsg.id,
        result: { status: "ok", memoryMB: 128 },
      };
      mockChild.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));

      await healthPromise;

      expect(manager.processes["test-agent"]._lastMemoryMB).toBe(128);
    });

    it("should not throw on health check failure", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      await startMockAgent(manager);

      // _healthCheck calls sendJsonRpc which will time out
      const healthPromise = manager._healthCheck("test-agent");

      jest.advanceTimersByTime(10001); // health check uses 10s timeout

      // Should resolve without error (just warns)
      await healthPromise;

      jest.useRealTimers();
    });

    it("should not run health check if agent is not in processes", async () => {
      const { manager } = createManager();

      // Should not throw
      await manager._healthCheck("nonexistent");
    });

    it("should start periodic health checks on agent start", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      await startMockAgent(manager);

      const entry = manager.processes["test-agent"];
      expect(entry.healthTimer).not.toBeNull();

      jest.useRealTimers();
    });
  });

  // ── 8. Config / Manifest ──────────────────────────────────────────────

  describe("Config and Manifest", () => {
    it("should load manifest from first available path", () => {
      const { manager } = createManager();
      expect(manager.manifest).toEqual(SAMPLE_MANIFEST);
    });

    it("should return empty manifest if no file found", () => {
      ipcMain._handlers.clear();
      mockFsImpl.existsSync.mockReturnValue(false);

      const mainWindow = new BrowserWindow();
      const manager = new AgentProcessManager(mainWindow);

      expect(manager.manifest).toEqual({ manifest_version: 1, agents: [] });
    });

    it("should handle malformed manifest JSON gracefully", () => {
      ipcMain._handlers.clear();
      mockFsImpl.existsSync.mockReturnValue(true);
      mockFsImpl.readFileSync.mockReturnValue("not valid json {{{");

      const mainWindow = new BrowserWindow();
      const manager = new AgentProcessManager(mainWindow);

      // Should fall back to empty manifest
      expect(manager.manifest).toEqual({ manifest_version: 1, agents: [] });
    });

    it("should reload manifest on reloadManifest()", () => {
      const { manager } = createManager();

      const updatedManifest = {
        manifest_version: 1,
        agents: [{ id: "new-agent", name: "New", binaries: {} }],
      };
      mockFsImpl.readFileSync.mockReturnValue(JSON.stringify(updatedManifest));

      const result = manager.reloadManifest();

      expect(result.agents).toHaveLength(1);
      expect(result.agents[0].id).toBe("new-agent");
      expect(manager.manifest).toBe(result);
    });

    it("should return manifest via getManifest()", () => {
      const { manager } = createManager();
      const manifest = manager.getManifest();
      expect(manifest).toBe(manager.manifest);
    });

    it("should load config from tray-config.json", () => {
      const { manager } = createManager();
      expect(manager.config.agents["test-agent"].autoStart).toBe(true);
    });

    it("should return default config on read error", () => {
      ipcMain._handlers.clear();
      mockFsImpl.existsSync.mockImplementation((p) => {
        if (typeof p === "string" && p.includes("tray-config.json")) return true;
        return false;
      });
      mockFsImpl.readFileSync.mockImplementation(() => {
        throw new Error("read error");
      });

      const mainWindow = new BrowserWindow();
      const manager = new AgentProcessManager(mainWindow);

      expect(manager.config).toEqual({ agents: {}, tray: {} });
    });

    it("should look up agent info by id in manifest", () => {
      const { manager } = createManager();

      const info = manager._getAgentInfo("test-agent");
      expect(info).toBeDefined();
      expect(info.id).toBe("test-agent");
      expect(info.name).toBe("Test Agent");
    });

    it("should return null for unknown agent id", () => {
      const { manager } = createManager();
      const info = manager._getAgentInfo("unknown-agent");
      expect(info).toBeNull();
    });

    it("should return null for agent info when manifest has no agents array", () => {
      const { manager } = createManager({ manifest: {} });
      const info = manager._getAgentInfo("test-agent");
      expect(info).toBeNull();
    });

    it("should resolve binary path using platform-specific binaries", () => {
      const { manager } = createManager();
      const agentInfo = {
        id: "my-agent",
        binaries: { win32: "my-agent.exe", darwin: "my-agent", linux: "my-agent" },
      };

      const result = manager._resolveBinaryPath(agentInfo);
      // Should contain the agent id and the platform-specific binary name
      expect(result).toContain("my-agent");
      // Should be under the agents directory
      expect(result).toContain("agents");
    });

    it("should return null binary path if platform is not in binaries", () => {
      const { manager } = createManager();
      const agentInfo = {
        id: "other-agent",
        binaries: { unsupported_platform: "binary" },
      };

      const result = manager._resolveBinaryPath(agentInfo);
      expect(result).toBeNull();
    });
  });

  // ── 9. Status queries ─────────────────────────────────────────────────

  describe("Status queries", () => {
    it("should return not-running status for an agent that is not started", () => {
      const { manager } = createManager();

      const status = manager.getAgentStatus("test-agent");
      expect(status.running).toBe(false);
      expect(status.installed).toBe(true); // binary exists per our mock
    });

    it("should return running status with pid and uptime", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      const status = manager.getAgentStatus("test-agent");
      expect(status.running).toBe(true);
      expect(status.pid).toBe(mockChild.pid);
      expect(typeof status.uptime).toBe("number");
      expect(status.uptime).toBeGreaterThanOrEqual(0);
      expect(status.installed).toBe(true);
    });

    it("should include memoryMB when available from health check", async () => {
      const { manager } = createManager();
      await startMockAgent(manager);

      manager.processes["test-agent"]._lastMemoryMB = 256;

      const status = manager.getAgentStatus("test-agent");
      expect(status.memoryMB).toBe(256);
    });

    it("should return installed=false when binary does not exist", () => {
      const { manager } = createManager();

      mockFsImpl.existsSync.mockImplementation((p) => {
        if (typeof p === "string" && (p.includes("test-agent") || p.includes("second"))) {
          return false;
        }
        return true;
      });

      const status = manager.getAgentStatus("test-agent");
      expect(status.installed).toBe(false);
    });

    it("should return installed=false for agent not in manifest", () => {
      const { manager } = createManager();

      const status = manager.getAgentStatus("unknown-agent");
      expect(status.installed).toBe(false);
      expect(status.running).toBe(false);
    });

    it("should return statuses for all agents in manifest", async () => {
      const { manager } = createManager();
      await startMockAgent(manager, "test-agent");

      const allStatuses = manager.getAllAgentStatuses();

      expect(allStatuses["test-agent"]).toBeDefined();
      expect(allStatuses["test-agent"].running).toBe(true);
      expect(allStatuses["second-agent"]).toBeDefined();
      expect(allStatuses["second-agent"].running).toBe(false);
    });

    it("should include running agents not in manifest in getAllAgentStatuses", async () => {
      const { manager } = createManager();

      // Manually insert a rogue process entry
      manager.processes["rogue-agent"] = {
        process: { pid: 999 },
        startedAt: Date.now(),
        stderrBuffer: [],
        stdoutBuffer: "",
        rpcIdCounter: 1,
        pendingRpc: {},
        healthTimer: null,
        stopping: false,
      };

      const allStatuses = manager.getAllAgentStatuses();
      expect(allStatuses["rogue-agent"]).toBeDefined();
      expect(allStatuses["rogue-agent"].running).toBe(true);
    });
  });

  // ── 10. IPC handlers ──────────────────────────────────────────────────

  describe("IPC handlers", () => {
    it("should register agent:start handler that calls startAgent", async () => {
      const { manager } = createManager();
      const spy = jest.spyOn(manager, "startAgent").mockResolvedValue({ pid: 1234 });

      const result = await ipcMain.simulateInvoke("agent:start", "test-agent");
      expect(spy).toHaveBeenCalledWith("test-agent");
      expect(result).toEqual({ pid: 1234 });
    });

    it("should register agent:stop handler that calls stopAgent", async () => {
      const { manager } = createManager();
      const spy = jest.spyOn(manager, "stopAgent").mockResolvedValue(undefined);

      await ipcMain.simulateInvoke("agent:stop", "test-agent");
      expect(spy).toHaveBeenCalledWith("test-agent");
    });

    it("should register agent:restart handler that calls restartAgent", async () => {
      const { manager } = createManager();
      const spy = jest.spyOn(manager, "restartAgent").mockResolvedValue({ pid: 5678 });

      const result = await ipcMain.simulateInvoke("agent:restart", "test-agent");
      expect(spy).toHaveBeenCalledWith("test-agent");
      expect(result).toEqual({ pid: 5678 });
    });

    it("should register agent:status handler that calls getAgentStatus", async () => {
      const { manager } = createManager();

      const result = await ipcMain.simulateInvoke("agent:status", "test-agent");
      expect(result).toHaveProperty("running");
      expect(result).toHaveProperty("installed");
    });

    it("should register agent:status-all handler that calls getAllAgentStatuses", async () => {
      const { manager } = createManager();

      const result = await ipcMain.simulateInvoke("agent:status-all");
      expect(result).toHaveProperty("test-agent");
      expect(result).toHaveProperty("second-agent");
    });

    it("should register agent:send-rpc handler that calls sendJsonRpc", async () => {
      const { manager } = createManager();
      const spy = jest.spyOn(manager, "sendJsonRpc").mockResolvedValue({ ok: true });

      const result = await ipcMain.simulateInvoke(
        "agent:send-rpc",
        "test-agent",
        "my-method",
        { param: 1 }
      );
      expect(spy).toHaveBeenCalledWith("test-agent", "my-method", { param: 1 });
      expect(result).toEqual({ ok: true });
    });

    it("should register agent:get-manifest handler that returns manifest", async () => {
      const { manager } = createManager();

      const result = await ipcMain.simulateInvoke("agent:get-manifest");
      expect(result).toEqual(SAMPLE_MANIFEST);
    });

    it("should register agent:install handler that throws not-implemented", async () => {
      createManager();

      await expect(
        ipcMain.simulateInvoke("agent:install", "test-agent")
      ).rejects.toThrow("Agent installation not yet implemented");
    });

    it("should register agent:uninstall handler that throws not-implemented", async () => {
      createManager();

      await expect(
        ipcMain.simulateInvoke("agent:uninstall", "test-agent")
      ).rejects.toThrow("Agent uninstallation not yet implemented");
    });
  });

  // ── 11. Bulk operations ───────────────────────────────────────────────

  describe("Bulk operations", () => {
    describe("startAllEnabled", () => {
      it("should start agents marked with autoStart=true", async () => {
        const { manager } = createManager();

        // Mock startAgent to avoid real spawn
        const spy = jest.spyOn(manager, "startAgent").mockResolvedValue({ pid: 1 });

        await manager.startAllEnabled();

        // Only test-agent has autoStart=true
        expect(spy).toHaveBeenCalledWith("test-agent");
        expect(spy).not.toHaveBeenCalledWith("second-agent");
      });

      it("should not start agents that are already running", async () => {
        const { manager } = createManager();

        // Pretend test-agent is already running
        manager.processes["test-agent"] = {
          process: { pid: 111 },
          startedAt: Date.now(),
          stderrBuffer: [],
          stdoutBuffer: "",
          rpcIdCounter: 1,
          pendingRpc: {},
          healthTimer: null,
          stopping: false,
        };

        const spy = jest.spyOn(manager, "startAgent");

        await manager.startAllEnabled();

        expect(spy).not.toHaveBeenCalled();
      });

      it("should emit agent-start-failed on start error", async () => {
        const { manager } = createManager();

        jest.spyOn(manager, "startAgent").mockRejectedValue(new Error("spawn failed"));

        const failEvents = [];
        manager.on("agent-start-failed", (id, msg) => failEvents.push({ id, msg }));

        await manager.startAllEnabled();

        expect(failEvents).toHaveLength(1);
        expect(failEvents[0].id).toBe("test-agent");
        expect(failEvents[0].msg).toBe("spawn failed");
      });

      it("should do nothing if config has no agents", async () => {
        const { manager } = createManager({
          config: { agents: {}, tray: {} },
        });

        const spy = jest.spyOn(manager, "startAgent");
        await manager.startAllEnabled();
        expect(spy).not.toHaveBeenCalled();
      });
    });

    describe("stopAll", () => {
      it("should stop all running agents", async () => {
        const { manager } = createManager();

        const spy = jest.spyOn(manager, "stopAgent").mockResolvedValue(undefined);

        // Pretend two agents are running
        manager.processes["test-agent"] = { process: { pid: 1 } };
        manager.processes["second-agent"] = { process: { pid: 2 } };

        await manager.stopAll();

        expect(spy).toHaveBeenCalledWith("test-agent");
        expect(spy).toHaveBeenCalledWith("second-agent");
      });

      it("should handle no running agents gracefully", async () => {
        const { manager } = createManager();

        // Should not throw
        await manager.stopAll();
      });
    });
  });

  // ── 12. Edge cases ────────────────────────────────────────────────────

  describe("Edge cases", () => {
    it("should not crash _sendToRenderer if mainWindow is destroyed", async () => {
      const { manager, mainWindow } = createManager();
      mainWindow.close(); // sets _isDestroyed = true

      // Should not throw
      manager._emitStatusChange("test-agent", "stopped");
    });

    it("should not crash _sendToRenderer if mainWindow is null", () => {
      ipcMain._handlers.clear();
      setupFsMocks();
      const manager = new AgentProcessManager(null);

      // Should not throw
      manager._emitStatusChange("test-agent", "stopped");
    });

    it("should reject all pending RPCs on process cleanup", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Create some pending RPCs
      const rpc1 = manager.sendJsonRpc("test-agent", "method1", {}).catch((e) => e);
      const rpc2 = manager.sendJsonRpc("test-agent", "method2", {}).catch((e) => e);

      // Trigger unexpected exit which causes cleanup
      mockChild.emit("exit", 1, null);

      const err1 = await rpc1;
      const err2 = await rpc2;

      expect(err1).toBeInstanceOf(Error);
      expect(err1.message).toContain("process exited");
      expect(err2).toBeInstanceOf(Error);
      expect(err2.message).toContain("process exited");
    });

    it("should handle _cleanupProcess being called twice (idempotent)", async () => {
      const { manager } = createManager();
      await startMockAgent(manager);

      manager._cleanupProcess("test-agent");
      // Second call should not throw
      manager._cleanupProcess("test-agent");

      expect(manager.processes["test-agent"]).toBeUndefined();
    });

    it("should handle _waitForExit when process has already exited", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.exitCode = 0;

      const result = await manager._waitForExit("test-agent", 1000);
      expect(result).toBe(true);
    });

    it("should resolve _waitForExit(true) when agent not in processes", async () => {
      const { manager } = createManager();

      const result = await manager._waitForExit("nonexistent", 1000);
      expect(result).toBe(true);
    });

    it("should handle _waitForExit timeout correctly", async () => {
      jest.useFakeTimers();
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      mockChild.exitCode = null; // hasn't exited

      const waitPromise = manager._waitForExit("test-agent", 3000);

      jest.advanceTimersByTime(3001);

      const result = await waitPromise;
      expect(result).toBe(false);

      // Clean up: clear any remaining health-check intervals before restoring real timers
      jest.clearAllTimers();
      manager._cleanupProcess("test-agent");
      jest.useRealTimers();
    });

    it("should keep only last 100 crash log entries", async () => {
      const { manager } = createManager();

      // Mock an existing crash log with 100 entries
      const existingLog = Array.from({ length: 100 }, (_, i) => ({
        agentId: "old",
        exitCode: 1,
        signal: null,
        timestamp: `2025-01-${String(i + 1).padStart(2, "0")}`,
      }));

      mockFsImpl.existsSync.mockImplementation((p) => {
        if (typeof p === "string" && p.includes("crash-log.json")) return true;
        if (typeof p === "string" && p.includes(".gaia")) return true;
        return false;
      });
      mockFsImpl.readFileSync.mockImplementation((p) => {
        if (typeof p === "string" && p.includes("crash-log.json")) {
          return JSON.stringify(existingLog);
        }
        return "{}";
      });

      manager._logCrash("new-agent", 42, "SIGTERM");

      const writeCall = mockFsImpl.writeFileSync.mock.calls[0];
      const logData = JSON.parse(writeCall[1]);
      expect(logData).toHaveLength(100);
      expect(logData[logData.length - 1].agentId).toBe("new-agent");
      // Oldest entry should have been trimmed
      expect(logData[0].agentId).toBe("old");
      expect(logData[0].timestamp).toBe("2025-01-02"); // first was removed
    });

    it("should create .gaia directory for crash log if it does not exist", async () => {
      const { manager } = createManager();

      mockFsImpl.existsSync.mockImplementation((p) => {
        if (typeof p === "string" && p.includes("crash-log.json")) return false;
        if (typeof p === "string" && p.includes(".gaia")) return false;
        return false;
      });

      manager._logCrash("test-agent", 1, null);

      expect(mockFsImpl.mkdirSync).toHaveBeenCalledWith(
        expect.stringContaining(".gaia"),
        { recursive: true }
      );
    });

    it("should gracefully handle crash log write failure", async () => {
      const { manager } = createManager();

      mockFsImpl.existsSync.mockReturnValue(false);
      mockFsImpl.writeFileSync.mockImplementation(() => {
        throw new Error("EACCES");
      });

      // Should not throw
      manager._logCrash("test-agent", 1, null);
    });

    it("should handle stopAgent when stdin write throws", async () => {
      const { manager } = createManager();
      const { mockChild } = await startMockAgent(manager);

      // Make stdin.write throw
      mockChild.stdin.write.mockImplementation(() => {
        throw new Error("EPIPE");
      });
      mockChild.stdin.destroyed = false; // not destroyed, just broken

      // stopAgent should still proceed without throwing
      // Simulate process exiting after the EPIPE
      const stopPromise = manager.stopAgent("test-agent");
      mockChild.exitCode = 1;
      mockChild.emit("exit", 1, null);
      await stopPromise;

      // Should not throw despite EPIPE
      expect(true).toBe(true);
    });

    it("should emit status-change event on the EventEmitter", async () => {
      const { manager } = createManager();
      const events = [];
      manager.on("status-change", (payload) => events.push(payload));

      manager._emitStatusChange("test-agent", "running", "started successfully");

      expect(events).toHaveLength(1);
      expect(events[0]).toEqual(
        expect.objectContaining({
          agentId: "test-agent",
          status: "running",
          detail: "started successfully",
          timestamp: expect.any(Number),
        })
      );
    });

    it("should send status-change to renderer via webContents.send", async () => {
      const { manager, mainWindow } = createManager();

      manager._emitStatusChange("test-agent", "error", "something failed");

      expect(mainWindow.webContents.send).toHaveBeenCalledWith(
        "agent:status-change",
        expect.objectContaining({
          agentId: "test-agent",
          status: "error",
          detail: "something failed",
        })
      );
    });

    it("should handle multiple agents running concurrently", async () => {
      const { manager } = createManager();

      const child1 = mockCreateChildProcess();
      mockSpawnHolder.returnValue = child1;
      await manager.startAgent("test-agent");

      const child2 = mockCreateChildProcess();
      mockSpawnHolder.returnValue = child2;
      await manager.startAgent("second-agent");

      expect(Object.keys(manager.processes)).toHaveLength(2);
      expect(manager.processes["test-agent"].process).toBe(child1);
      expect(manager.processes["second-agent"].process).toBe(child2);

      // Each has their own independent state
      expect(manager.processes["test-agent"].rpcIdCounter).toBe(1);
      expect(manager.processes["second-agent"].rpcIdCounter).toBe(1);
    });
  });
});
