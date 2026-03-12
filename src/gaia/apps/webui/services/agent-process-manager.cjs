// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — Agent Process Manager (T2)
 *
 * Manages OS agent subprocesses (C++ MCP servers, .NET agents, Python agents).
 * Each agent communicates via JSON-RPC 2.0 over stdio:
 *   - stdout → JSON-RPC messages (MCP protocol + GAIA extensions)
 *   - stderr → Structured log lines → piped to terminal view
 *   - stdin  → JSON-RPC requests from the tray app
 *
 * Cross-platform shutdown protocol (C3 fix):
 *   1. Send JSON-RPC {"method": "shutdown"} via stdin
 *   2. Wait up to 5s for clean exit
 *   3. Force kill as last resort
 *
 * Health checking uses {"method": "ping"} (S1 fix), NOT "initialize".
 *
 * Config stored in ~/.gaia/tray-config.json (S2 fix).
 */

const { spawn } = require("child_process");
const { ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { EventEmitter } = require("events");

// ── Constants ────────────────────────────────────────────────────────────

const GAIA_DIR = path.join(os.homedir(), ".gaia");
const AGENTS_DIR = path.join(GAIA_DIR, "agents");
const CONFIG_PATH = path.join(GAIA_DIR, "tray-config.json");
const CRASH_LOG_PATH = path.join(GAIA_DIR, "crash-log.json");
const MANIFEST_FILENAME = "agent-manifest.json";

/** Graceful shutdown timeout before force kill (ms) */
const SHUTDOWN_TIMEOUT = 5000;

/** Health check interval (ms) — uses MCP "ping", not "initialize" */
const HEALTH_CHECK_INTERVAL = 30000;

/** Delay between sequential auto-starts (ms) */
const AUTO_START_DELAY = 100;

/** Max crash restarts within the crash window */
const MAX_CRASH_RESTARTS = 3;

/** Crash window (ms) — max restarts counted within this period */
const CRASH_WINDOW = 60000;

/** Delay before crash restart (ms) */
const CRASH_RESTART_DELAY = 2000;

/** Max lines kept in stderr buffer per agent */
const STDERR_BUFFER_MAX = 10000;

/** Max bytes kept in stdout buffer per agent (protects against malformed output without newlines) */
const STDOUT_BUFFER_MAX = 1024 * 1024; // 1 MB

// ── AgentProcessManager ──────────────────────────────────────────────────

class AgentProcessManager extends EventEmitter {
  /**
   * @param {Electron.BrowserWindow} mainWindow — for sending IPC events to renderer
   */
  constructor(mainWindow) {
    super();

    /** @type {Electron.BrowserWindow} */
    this.mainWindow = mainWindow;

    /**
     * Running processes keyed by agentId.
     * @type {Record<string, {
     *   process: import('child_process').ChildProcess,
     *   startedAt: number,
     *   stderrBuffer: string[],
     *   stdoutBuffer: string,
     *   rpcIdCounter: number,
     *   pendingRpc: Record<string, { resolve: Function, reject: Function, timer: NodeJS.Timeout }>,
     *   healthTimer: NodeJS.Timeout | null,
     *   stopping: boolean,
     * }>}
     */
    this.processes = {};

    /** Crash timestamps per agent for rate-limiting restart attempts */
    this._crashTimes = {};

    /** Agent manifest (loaded from disk or fetched) */
    this.manifest = this._loadManifest();

    /** Tray config (for auto-start and crash-restart settings) */
    this.config = this._loadConfig();

    this._registerIpcHandlers();
  }

  // ── Public API: Lifecycle ────────────────────────────────────────────

  /**
   * Start an agent subprocess.
   * @param {string} agentId
   * @returns {Promise<{ pid: number }>}
   */
  async startAgent(agentId) {
    if (this.processes[agentId]) {
      console.log(`[agent-mgr] Agent ${agentId} is already running (PID ${this.processes[agentId].process.pid})`);
      return { pid: this.processes[agentId].process.pid };
    }

    const agentInfo = this._getAgentInfo(agentId);
    if (!agentInfo) {
      throw new Error(`Agent "${agentId}" not found in manifest`);
    }

    const binaryPath = this._resolveBinaryPath(agentInfo);
    if (!binaryPath || !fs.existsSync(binaryPath)) {
      throw new Error(
        `Agent binary not found: ${binaryPath || "(no binary for this platform)"}`
      );
    }

    console.log(`[agent-mgr] Starting agent ${agentId}: ${binaryPath}`);

    const child = spawn(binaryPath, ["--stdio"], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env },
      detached: false,
      // On Windows, don't create a console window for the subprocess
      windowsHide: true,
    });

    const entry = {
      process: child,
      startedAt: Date.now(),
      stderrBuffer: [],
      stdoutBuffer: "",
      rpcIdCounter: 1,
      pendingRpc: {},
      healthTimer: null,
      stopping: false, // Set to true during intentional shutdown to suppress crash recovery
    };

    this.processes[agentId] = entry;

    // ── stdout: JSON-RPC message stream ──
    child.stdout.on("data", (data) => {
      this._handleStdout(agentId, data);
    });

    // ── stderr: log lines ──
    child.stderr.on("data", (data) => {
      this._handleStderr(agentId, data);
    });

    // ── Process lifecycle events ──
    child.on("error", (err) => {
      console.error(`[agent-mgr] Agent ${agentId} spawn error:`, err.message);
      this._emitStatusChange(agentId, "error", err.message);
    });

    child.on("exit", (code, signal) => {
      console.log(
        `[agent-mgr] Agent ${agentId} exited (code=${code}, signal=${signal})`
      );
      this._handleProcessExit(agentId, code, signal);
    });

    // Start health check timer
    entry.healthTimer = setInterval(() => {
      this._healthCheck(agentId);
    }, HEALTH_CHECK_INTERVAL);

    this._emitStatusChange(agentId, "running");
    console.log(`[agent-mgr] Agent ${agentId} started (PID ${child.pid})`);

    return { pid: child.pid };
  }

  /**
   * Stop an agent gracefully using JSON-RPC shutdown protocol.
   * Cross-platform (works on Windows where SIGTERM = TerminateProcess).
   * @param {string} agentId
   * @returns {Promise<void>}
   */
  async stopAgent(agentId) {
    const entry = this.processes[agentId];
    if (!entry) {
      console.log(`[agent-mgr] Agent ${agentId} is not running`);
      return;
    }

    // Guard against concurrent stopAgent() calls for the same agent
    if (entry.stopping) {
      console.log(`[agent-mgr] Agent ${agentId} is already being stopped — skipping duplicate`);
      return;
    }

    console.log(`[agent-mgr] Stopping agent ${agentId} (PID ${entry.process.pid})...`);

    // Mark as intentionally stopping — suppresses crash recovery in _handleProcessExit
    entry.stopping = true;

    // Clear health check timer
    if (entry.healthTimer) {
      clearInterval(entry.healthTimer);
      entry.healthTimer = null;
    }

    // Step 1: Send JSON-RPC shutdown request via stdin
    try {
      this._sendJsonRpcRaw(agentId, "shutdown", {});
    } catch (err) {
      console.warn(
        `[agent-mgr] Could not send shutdown to ${agentId}:`,
        err.message
      );
    }

    // Step 2: Wait up to SHUTDOWN_TIMEOUT for clean exit
    const exited = await this._waitForExit(agentId, SHUTDOWN_TIMEOUT);

    // Step 3: Force kill if still running
    if (!exited && this.processes[agentId]) {
      console.warn(
        `[agent-mgr] Agent ${agentId} did not exit within ${SHUTDOWN_TIMEOUT}ms, force killing...`
      );
      try {
        entry.process.kill(); // SIGKILL on Unix, TerminateProcess on Windows
      } catch {
        // Already dead
      }
    }

    // Note: _handleProcessExit may have already cleaned up if the process exited.
    // _cleanupProcess is idempotent, so calling it again is safe.
    this._cleanupProcess(agentId);
    this._emitStatusChange(agentId, "stopped");
    console.log(`[agent-mgr] Agent ${agentId} stopped`);
  }

  /**
   * Restart an agent (stop + start).
   * @param {string} agentId
   */
  async restartAgent(agentId) {
    await this.stopAgent(agentId);
    return this.startAgent(agentId);
  }

  // ── Public API: Monitoring ───────────────────────────────────────────

  /**
   * Get the status of a single agent.
   * @param {string} agentId
   * @returns {{ installed: boolean, running: boolean, pid?: number, uptime?: number, memoryMB?: number }}
   */
  getAgentStatus(agentId) {
    const entry = this.processes[agentId];
    const agentInfo = this._getAgentInfo(agentId);
    const binaryPath = agentInfo
      ? this._resolveBinaryPath(agentInfo)
      : null;
    const installed = binaryPath ? fs.existsSync(binaryPath) : false;

    if (!entry) {
      return { installed, running: false };
    }

    const uptime = Math.floor((Date.now() - entry.startedAt) / 1000);
    let memoryMB = undefined;

    // Try to read memory usage (not available on all platforms)
    try {
      if (entry.process.pid) {
        // Node doesn't expose child memory directly, but we track it via health checks
        memoryMB = entry._lastMemoryMB || undefined;
      }
    } catch {
      // Ignore
    }

    return {
      installed,
      running: true,
      pid: entry.process.pid,
      uptime,
      memoryMB,
    };
  }

  /**
   * Get statuses for all known agents.
   * @returns {Record<string, object>}
   */
  getAllAgentStatuses() {
    const result = {};

    // Include agents from manifest
    if (this.manifest && this.manifest.agents) {
      for (const agent of this.manifest.agents) {
        result[agent.id] = this.getAgentStatus(agent.id);
      }
    }

    // Include any running agents not in manifest (shouldn't happen, but safety)
    for (const agentId of Object.keys(this.processes)) {
      if (!result[agentId]) {
        result[agentId] = this.getAgentStatus(agentId);
      }
    }

    return result;
  }

  // ── Public API: I/O ──────────────────────────────────────────────────

  /**
   * Send a JSON-RPC request to an agent and wait for the response.
   * @param {string} agentId
   * @param {string} method
   * @param {object} params
   * @param {number} [timeoutMs=30000]
   * @returns {Promise<any>}
   */
  sendJsonRpc(agentId, method, params = {}, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      const entry = this.processes[agentId];
      if (!entry) {
        reject(new Error(`Agent "${agentId}" is not running`));
        return;
      }

      const id = `rpc-${entry.rpcIdCounter++}`;

      const timer = setTimeout(() => {
        delete entry.pendingRpc[id];
        reject(new Error(`JSON-RPC timeout for ${method} (${timeoutMs}ms)`));
      }, timeoutMs);

      entry.pendingRpc[id] = { resolve, reject, timer };

      this._sendJsonRpcRaw(agentId, method, params, id);
    });
  }

  // ── Public API: Bulk operations ──────────────────────────────────────

  /**
   * Start all agents marked as auto-start in config.
   */
  async startAllEnabled() {
    const agentConfigs = this.config.agents || {};

    for (const [agentId, agentCfg] of Object.entries(agentConfigs)) {
      if (agentCfg.autoStart && !this.processes[agentId]) {
        try {
          await this.startAgent(agentId);
          // Stagger starts to avoid resource spike
          await new Promise((r) => setTimeout(r, AUTO_START_DELAY));
        } catch (err) {
          console.error(
            `[agent-mgr] Failed to auto-start ${agentId}:`,
            err.message
          );
          this.emit("agent-start-failed", agentId, err.message);
        }
      }
    }
  }

  /**
   * Stop all running agents gracefully.
   */
  async stopAll() {
    const agentIds = Object.keys(this.processes);
    console.log(
      `[agent-mgr] Stopping all agents: ${agentIds.join(", ") || "(none)"}`
    );
    await Promise.all(agentIds.map((id) => this.stopAgent(id)));
  }

  // ── Public API: Manifest ─────────────────────────────────────────────

  /** @returns {object | null} The agent manifest */
  getManifest() {
    return this.manifest;
  }

  /** Reload the manifest from disk. */
  reloadManifest() {
    this.manifest = this._loadManifest();
    return this.manifest;
  }

  // ── Private: stdout handling (JSON-RPC) ──────────────────────────────

  _handleStdout(agentId, data) {
    const entry = this.processes[agentId];
    if (!entry) return;

    // Buffer incoming data and split on newlines (JSON-RPC uses newline-delimited JSON)
    entry.stdoutBuffer += data.toString();

    // Safety: cap buffer size to prevent memory leak from malformed output without newlines
    if (entry.stdoutBuffer.length > STDOUT_BUFFER_MAX) {
      console.warn(
        `[agent-mgr] stdout buffer for ${agentId} exceeded ${STDOUT_BUFFER_MAX} bytes — discarding`
      );
      entry.stdoutBuffer = "";
    }

    let newlineIdx;
    while ((newlineIdx = entry.stdoutBuffer.indexOf("\n")) !== -1) {
      const line = entry.stdoutBuffer.slice(0, newlineIdx).trim();
      entry.stdoutBuffer = entry.stdoutBuffer.slice(newlineIdx + 1);

      if (!line) continue;

      try {
        const msg = JSON.parse(line);
        this._handleJsonRpcMessage(agentId, msg);
      } catch (err) {
        console.warn(
          `[agent-mgr] Non-JSON stdout from ${agentId}: ${line.slice(0, 200)}`
        );
      }
    }
  }

  _handleJsonRpcMessage(agentId, msg) {
    // Check if this is a response to a pending RPC call
    if (msg.id && this.processes[agentId]) {
      const pending = this.processes[agentId].pendingRpc[msg.id];
      if (pending) {
        clearTimeout(pending.timer);
        delete this.processes[agentId].pendingRpc[msg.id];

        if (msg.error) {
          pending.reject(
            new Error(msg.error.message || JSON.stringify(msg.error))
          );
        } else {
          pending.resolve(msg.result);
        }
        return;
      }
    }

    // It's a notification or unsolicited message — forward to renderer
    this._sendToRenderer("agent:stdout", {
      agentId,
      message: msg,
      timestamp: Date.now(),
    });

    // Handle specific notification methods
    if (msg.method === "notification/send") {
      this.emit("agent-notification", agentId, msg.params);
    }
  }

  // ── Private: stderr handling (log lines) ─────────────────────────────

  _handleStderr(agentId, data) {
    const entry = this.processes[agentId];
    if (!entry) return;

    const lines = data.toString().split("\n");
    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line) continue;

      // Add to circular buffer
      entry.stderrBuffer.push(line);
      if (entry.stderrBuffer.length > STDERR_BUFFER_MAX) {
        entry.stderrBuffer.shift();
      }

      // Forward to renderer
      this._sendToRenderer("agent:stderr", {
        agentId,
        line,
        timestamp: Date.now(),
      });
    }
  }

  // ── Private: Process exit & crash recovery ───────────────────────────

  _handleProcessExit(agentId, code, signal) {
    const entry = this.processes[agentId];

    // If the agent was intentionally stopped via stopAgent(), skip crash handling.
    // stopAgent() will handle cleanup and status-change emission itself.
    if (entry && entry.stopping) {
      console.log(`[agent-mgr] Agent ${agentId} exited during intentional stop — skipping crash handler`);
      return;
    }

    // Unexpected exit — log and handle crash recovery
    if (code !== 0 && code !== null) {
      this._logCrash(agentId, code, signal);
    }

    // Notify renderer of unexpected exit
    this._sendToRenderer("agent:crashed", {
      agentId,
      exitCode: code,
      signal,
      timestamp: Date.now(),
    });

    this._cleanupProcess(agentId);
    this._emitStatusChange(agentId, "stopped");

    // Check if crash recovery is enabled (only for non-zero exits)
    const agentConfig = (this.config.agents || {})[agentId] || {};
    if (agentConfig.restartOnCrash && code !== 0) {
      this._attemptCrashRestart(agentId);
    }
  }

  _attemptCrashRestart(agentId) {
    // Track crash times for rate limiting
    const now = Date.now();
    const recentCrashes = this._crashTimes[agentId] || [];

    // Filter to crashes within the window
    const windowCrashes = recentCrashes.filter(
      (t) => now - t < CRASH_WINDOW
    );
    windowCrashes.push(now);
    this._crashTimes[agentId] = windowCrashes;

    if (windowCrashes.length > MAX_CRASH_RESTARTS) {
      console.warn(
        `[agent-mgr] Agent ${agentId} crashed ${windowCrashes.length} times in ${CRASH_WINDOW / 1000}s — NOT restarting`
      );
      this.emit("agent-crash-limit", agentId, windowCrashes.length);
      return;
    }

    console.log(
      `[agent-mgr] Agent ${agentId} crashed — restarting in ${CRASH_RESTART_DELAY}ms (attempt ${windowCrashes.length}/${MAX_CRASH_RESTARTS})`
    );

    setTimeout(async () => {
      try {
        await this.startAgent(agentId);
        console.log(`[agent-mgr] Agent ${agentId} restarted after crash`);
      } catch (err) {
        console.error(
          `[agent-mgr] Failed to restart ${agentId} after crash:`,
          err.message
        );
      }
    }, CRASH_RESTART_DELAY);
  }

  // ── Private: JSON-RPC wire protocol ──────────────────────────────────

  /**
   * Send a raw JSON-RPC message via stdin.
   * @param {string} agentId
   * @param {string} method
   * @param {object} params
   * @param {string} [id] — if provided, it's a request; if omitted, a notification
   */
  _sendJsonRpcRaw(agentId, method, params, id) {
    const entry = this.processes[agentId];
    if (!entry || !entry.process.stdin || entry.process.stdin.destroyed) {
      throw new Error(`Cannot write to stdin of agent "${agentId}"`);
    }

    const msg = {
      jsonrpc: "2.0",
      method,
      params: params || {},
    };
    if (id) msg.id = id;

    const payload = JSON.stringify(msg) + "\n";
    entry.process.stdin.write(payload);
  }

  // ── Private: Health check ────────────────────────────────────────────

  async _healthCheck(agentId) {
    if (!this.processes[agentId]) return;

    try {
      const result = await this.sendJsonRpc(agentId, "ping", {}, 10000);
      // Agent is healthy
      if (result && typeof result.memoryMB === "number") {
        this.processes[agentId]._lastMemoryMB = result.memoryMB;
      }
    } catch (err) {
      console.warn(
        `[agent-mgr] Health check failed for ${agentId}:`,
        err.message
      );
    }
  }

  // ── Private: Process cleanup ─────────────────────────────────────────

  _cleanupProcess(agentId) {
    const entry = this.processes[agentId];
    if (!entry) return;

    // Clear health check timer
    if (entry.healthTimer) {
      clearInterval(entry.healthTimer);
      entry.healthTimer = null;
    }

    // Reject any pending RPC calls
    for (const [rpcId, pending] of Object.entries(entry.pendingRpc)) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`Agent "${agentId}" process exited`));
    }

    delete this.processes[agentId];
  }

  /**
   * Wait for an agent process to exit within a timeout.
   * @returns {Promise<boolean>} true if exited, false if timed out
   */
  _waitForExit(agentId, timeoutMs) {
    return new Promise((resolve) => {
      const entry = this.processes[agentId];
      if (!entry) {
        resolve(true);
        return;
      }

      // Check if process already exited (exitCode is set once the process exits)
      if (entry.process.exitCode !== null) {
        resolve(true);
        return;
      }

      const timer = setTimeout(() => {
        entry.process.removeListener("exit", onExit);
        resolve(false);
      }, timeoutMs);

      const onExit = () => {
        clearTimeout(timer);
        resolve(true);
      };

      entry.process.once("exit", onExit);
    });
  }

  // ── Private: Events ──────────────────────────────────────────────────

  _emitStatusChange(agentId, status, detail) {
    const payload = {
      agentId,
      status,
      detail,
      timestamp: Date.now(),
    };

    this._sendToRenderer("agent:status-change", payload);
    this.emit("status-change", payload);
  }

  _sendToRenderer(channel, data) {
    try {
      if (this.mainWindow && !this.mainWindow.isDestroyed()) {
        this.mainWindow.webContents.send(channel, data);
      }
    } catch (err) {
      // Window may be closing
      console.warn(`[agent-mgr] Could not send to renderer:`, err.message);
    }
  }

  // ── Private: Manifest & config ───────────────────────────────────────

  _loadManifest() {
    // Try multiple locations
    const candidates = [
      path.join(__dirname, "..", MANIFEST_FILENAME), // alongside main.cjs
      path.join(GAIA_DIR, MANIFEST_FILENAME), // ~/.gaia/
      path.join(AGENTS_DIR, MANIFEST_FILENAME), // ~/.gaia/agents/
    ];

    for (const candidate of candidates) {
      try {
        if (fs.existsSync(candidate)) {
          const raw = fs.readFileSync(candidate, "utf8");
          const manifest = JSON.parse(raw);
          console.log(`[agent-mgr] Loaded manifest from ${candidate}`);
          return manifest;
        }
      } catch (err) {
        console.warn(
          `[agent-mgr] Error reading manifest from ${candidate}:`,
          err.message
        );
      }
    }

    console.log("[agent-mgr] No agent manifest found — starting with empty manifest");
    return { manifest_version: 1, agents: [] };
  }

  _loadConfig() {
    try {
      if (fs.existsSync(CONFIG_PATH)) {
        const raw = fs.readFileSync(CONFIG_PATH, "utf8");
        return JSON.parse(raw);
      }
    } catch (err) {
      console.warn("[agent-mgr] Could not load config:", err.message);
    }
    return { agents: {}, tray: {} };
  }

  _getAgentInfo(agentId) {
    if (!this.manifest || !this.manifest.agents) return null;
    return this.manifest.agents.find((a) => a.id === agentId) || null;
  }

  _resolveBinaryPath(agentInfo) {
    const platform = process.platform; // "win32", "darwin", "linux"
    const binaryName =
      agentInfo.binaries && agentInfo.binaries[platform];

    if (!binaryName) return null;

    // Check in ~/.gaia/agents/{agentId}/
    return path.join(AGENTS_DIR, agentInfo.id, binaryName);
  }

  // ── Private: Crash logging ───────────────────────────────────────────

  _logCrash(agentId, code, signal) {
    try {
      let crashLog = [];
      if (fs.existsSync(CRASH_LOG_PATH)) {
        crashLog = JSON.parse(fs.readFileSync(CRASH_LOG_PATH, "utf8"));
      }

      crashLog.push({
        agentId,
        exitCode: code,
        signal,
        timestamp: new Date().toISOString(),
      });

      // Keep last 100 entries
      if (crashLog.length > 100) {
        crashLog = crashLog.slice(-100);
      }

      if (!fs.existsSync(GAIA_DIR)) {
        fs.mkdirSync(GAIA_DIR, { recursive: true });
      }
      fs.writeFileSync(CRASH_LOG_PATH, JSON.stringify(crashLog, null, 2), "utf8");
    } catch (err) {
      console.warn("[agent-mgr] Could not write crash log:", err.message);
    }
  }

  // ── Private: IPC handlers ────────────────────────────────────────────

  _registerIpcHandlers() {
    ipcMain.handle("agent:start", async (_event, agentId) => {
      return this.startAgent(agentId);
    });

    ipcMain.handle("agent:stop", async (_event, agentId) => {
      return this.stopAgent(agentId);
    });

    ipcMain.handle("agent:restart", async (_event, agentId) => {
      return this.restartAgent(agentId);
    });

    ipcMain.handle("agent:status", (_event, agentId) => {
      return this.getAgentStatus(agentId);
    });

    ipcMain.handle("agent:status-all", () => {
      return this.getAllAgentStatuses();
    });

    ipcMain.handle("agent:send-rpc", async (_event, agentId, method, params) => {
      return this.sendJsonRpc(agentId, method, params);
    });

    ipcMain.handle("agent:get-manifest", () => {
      return this.getManifest();
    });

    ipcMain.handle("agent:install", async (_event, agentId) => {
      // TODO: T7 — agent installer integration
      throw new Error("Agent installation not yet implemented");
    });

    ipcMain.handle("agent:uninstall", async (_event, agentId) => {
      // TODO: T7 — agent uninstaller integration
      throw new Error("Agent uninstallation not yet implemented");
    });
  }
}

module.exports = AgentProcessManager;
