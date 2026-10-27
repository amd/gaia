// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Dev servers the agent starts, owned by main so they outlive turns and
 * sidecar restarts and stop when the app quits. Python parses launch.json
 * and asks the user; this file only runs the spec Python sends, argv only,
 * never through a shell.
 *
 * Servers are app-wide, but each remembers the chats that use it: the one
 * that started it first, then any that asked for the same server since.
 *
 * A ledger file records each server's pids and command lines, so the next
 * launch can stop servers a crash left behind. It only ever signals a pid it
 * recorded whose command line still matches.
 */

"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");
const { BridgeError } = require("./bridge-error.cjs");
const { descendantsFromTable, killProcessTree, listProcesses } = require("../process-tree.cjs");

/** @typedef {import("./contract").DevServerSpec} DevServerSpec */
/** @typedef {import("./contract").DevServerState} DevServerState */

/** Names a launch.json may set. Python's dev_servers.ENV_NAMES is the same; both test bridge/dev_server_env.json. */
const ENV_NAMES = new Set([
  "BROWSER", "CI", "DEBUG", "FORCE_COLOR", "HOST", "HOSTNAME", "LOG_LEVEL",
  "NODE_ENV", "NO_COLOR", "PORT", "PYTHONUNBUFFERED", "TZ",
]);
const ENV_PREFIXES = ["VITE_", "NEXT_PUBLIC_", "REACT_APP_", "PUBLIC_"];

/** What a server inherits from the app's own environment: enough to find and run its tools, no secrets. */
const INHERITED_ENV = [
  "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "TERM",
  "NVM_DIR", "NVM_BIN", "VOLTA_HOME", "PNPM_HOME", "COREPACK_HOME",
  "SystemRoot", "SYSTEMROOT", "ComSpec", "COMSPEC", "PATHEXT", "WINDIR", "APPDATA", "LOCALAPPDATA", "USERPROFILE",
  "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
];

const LOG_CAP_BYTES = 1024 * 1024;
const FAIL_LINES = 30;
const LINE_CHARS = 500;
const PROBE_EVERY_MS = 100;
const PROBE_TIMEOUT_MS = 400;
const REAP_GRACE_MS = 3000;
const ERROR_LINE = /\berror\b|\bfailed\b|\bexception\b|\bfatal\b|\bpanic\b|ERR!/i;

function envNameAllowed(name) {
  return ENV_NAMES.has(name) || (ENV_PREFIXES.some((p) => name.startsWith(p)) && /^[A-Z0-9_]+$/.test(name));
}

/**
 * @param {DevServerSpec} spec @param {NodeJS.ProcessEnv} from
 * @param {string | null} searchPath The backend's login-shell PATH; an app opened from the Dock has only launchd's.
 */
function childEnv(spec, from, searchPath = null) {
  const env = {};
  for (const name of INHERITED_ENV) if (from[name] !== undefined) env[name] = from[name];
  if (searchPath) env.PATH = searchPath;
  return { ...env, ...spec.env };
}

/** True when something accepts a TCP connection on the port, over IPv4 or IPv6 loopback. */
function portAnswers(port) {
  const one = (host) =>
    new Promise((resolve) => {
      const socket = net.connect({ host, port });
      const done = (ok) => {
        socket.destroy();
        resolve(ok);
      };
      socket.setTimeout(PROBE_TIMEOUT_MS, () => done(false));
      socket.once("connect", () => done(true));
      socket.once("error", () => done(false));
    });
  return Promise.all([one("127.0.0.1"), one("::1")]).then(([v4, v6]) => v4 || v6);
}

const sameSpec = (a, b) => JSON.stringify(normalized(a)) === JSON.stringify(normalized(b));
const normalized = (s) => ({ ...s, env: Object.fromEntries(Object.entries(s.env).sort()) });

/** One log file per server, cut back to its newest half when it passes the cap. */
class CappedLog {
  constructor(file, cap = LOG_CAP_BYTES) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    this.file = file;
    this.cap = cap;
    this.fd = fs.openSync(file, "w+");
    this.size = 0;
  }

  write(chunk) {
    if (this.fd === null) return;
    let buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(String(chunk));
    if (buf.length > this.cap / 2) buf = buf.subarray(buf.length - Math.floor(this.cap / 2));
    if (this.size + buf.length > this.cap) this.compact(buf.length);
    fs.writeSync(this.fd, buf, 0, buf.length, this.size);
    this.size += buf.length;
  }

  compact(incoming) {
    const keep = Math.max(0, Math.min(this.size, Math.floor(this.cap / 2) - incoming));
    const tail = Buffer.alloc(keep);
    fs.readSync(this.fd, tail, 0, keep, this.size - keep);
    const cut = tail.indexOf(10);
    const kept = cut >= 0 ? tail.subarray(cut + 1) : tail;
    const marker = Buffer.from("[GAIA: older lines were dropped to keep this log under 1 MB]\n");
    fs.ftruncateSync(this.fd, 0);
    fs.writeSync(this.fd, marker, 0, marker.length, 0);
    fs.writeSync(this.fd, kept, 0, kept.length, marker.length);
    this.size = marker.length + kept.length;
  }

  close() {
    if (this.fd === null) return;
    fs.closeSync(this.fd);
    this.fd = null;
  }
}

function readLines(file) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
  return text
    .split(/\r?\n/)
    .filter((l) => l.trim() !== "")
    .map((l) => (l.length > LINE_CHARS ? `${l.slice(0, LINE_CHARS)}…` : l));
}

class DevServerSupervisor {
  /**
   * @param {{
   *   dir: string,
   *   blockedPorts: () => number[],
   *   onChange: () => void,
   *   env?: NodeJS.ProcessEnv,
   *   spawn?: typeof childProcess.spawn,
   *   listProcesses?: typeof listProcesses,
   *   killTree?: typeof killProcessTree,
   *   reapGraceMs?: number,
   *   logger?: { log: Function, error: Function },
   * }} deps
   */
  constructor(deps) {
    this.deps = {
      env: process.env,
      spawn: childProcess.spawn,
      listProcesses,
      killTree: killProcessTree,
      reapGraceMs: REAP_GRACE_MS,
      logger: console,
      ...deps,
    };
    this.ledgerPath = path.join(deps.dir, "ledger.json");
    /** @type {Map<string, any>} */
    this.entries = new Map();
    /** @type {Map<string, Promise<unknown>>} */
    this.queues = new Map();
    /** @type {Promise<unknown> | null} */
    this.started = null;
  }

  /** Reap what a crash left behind. ensure waits for it, so a new server never races an orphan for its port. */
  start() {
    if (!this.started) {
      this.started = this.reapOrphans().catch((err) => {
        this.deps.logger.error(`[dev-servers] Could not stop dev servers left by an earlier run: ${err.message}. Its ledger stays at ${this.ledgerPath}.`);
      });
    }
    return this.started;
  }

  /** @returns {DevServerState[]} */
  list() {
    return [...this.entries.values()].map((e) => this.stateOf(e));
  }

  /**
   * Converge on "this spec runs and answers on its port".
   * @param {string} key @param {DevServerSpec} spec
   * @param {string} chat The chat whose agent asked.
   * @param {string | null} searchPath
   * @returns {Promise<{ server: DevServerState, reused: boolean, ms: number }>}
   */
  ensure(key, spec, chat, searchPath = null) {
    if (typeof chat !== "string" || chat === "") throw new Error(`A dev server must be started for a chat, got ${JSON.stringify(chat)}.`);
    return this.serial(key, async () => {
      if (!this.started) throw new BridgeError("internal", "Dev servers were used before main started them.");
      await this.started;
      const t0 = Date.now();
      const current = this.entries.get(key);
      if (current && current.status === "ready" && sameSpec(current.spec, spec)) {
        if (!current.chats.includes(chat)) {
          current.chats.push(chat);
          this.deps.onChange();
        }
        return { server: this.stateOf(current), reused: true, ms: Date.now() - t0 };
      }
      if (current && this.running(current)) await this.halt(current);
      await this.checkPort(key, spec);
      const entry = this.spawn(key, spec, chat, searchPath);
      try {
        await this.recordLedger();
        await this.untilReady(entry);
      } finally {
        await this.recordLedger();
      }
      return { server: this.stateOf(entry), reused: false, ms: Date.now() - t0 };
    });
  }

  /** @param {string} key */
  stop(key) {
    return this.serial(key, async () => {
      const entry = this.require(key);
      if (this.running(entry)) {
        await this.halt(entry);
        await this.recordLedger();
      }
      return this.stateOf(entry);
    });
  }

  /**
   * The newest lines of one server's log, optionally only errors or lines holding `pattern`.
   * @param {string} key
   * @param {{ errorsOnly: boolean, pattern: string | null, limit: number }} q
   */
  logs(key, { errorsOnly, pattern, limit }) {
    const entry = this.require(key);
    const all = readLines(entry.logFile);
    const needle = pattern ? pattern.toLowerCase() : null;
    const hits = all.filter((l) => (!errorsOnly || ERROR_LINE.test(l)) && (!needle || l.toLowerCase().includes(needle)));
    return { server: this.stateOf(entry), lines: hits.slice(-limit), matched: hits.length, total: all.length };
  }

  /** Quit: stop every server, then forget the ones that are gone. */
  async stopAll() {
    await Promise.all([...this.entries.values()].filter((e) => this.running(e)).map((e) => this.halt(e)));
    await this.recordLedger();
  }

  // ── Orphans ───────────────────────────────────────────────────────────

  /** Stop every recorded pid whose command line still matches; leave everything else alone. */
  async reapOrphans() {
    let ledger;
    try {
      ledger = JSON.parse(fs.readFileSync(this.ledgerPath, "utf8"));
    } catch (err) {
      if (err.code === "ENOENT") return { reaped: [] };
      throw new Error(`${this.ledgerPath} is unreadable (${err.message})`);
    }
    const recorded = (ledger.servers || []).flatMap((s) => s.processes || []);
    const matching = (table) => {
      const live = new Map(table.map((r) => [r.pid, r.command]));
      return recorded.filter((p) => p.pid !== process.pid && live.get(p.pid) === p.command);
    };
    const signal = (procs, sig) => {
      for (const p of procs) {
        try {
          process.kill(p.pid, sig);
        } catch (err) {
          if (err.code !== "ESRCH") this.deps.logger.error(`[dev-servers] ${sig} to orphan ${p.pid} failed: ${err.message}`);
        }
      }
    };
    const orphans = matching(await this.deps.listProcesses());
    if (orphans.length) {
      this.deps.logger.log(`[dev-servers] Stopping ${orphans.length} dev server process(es) left by an earlier run: ${orphans.map((p) => p.pid).join(", ")}`);
      signal(orphans, "SIGTERM");
      const deadline = Date.now() + this.deps.reapGraceMs;
      let left = orphans;
      while (left.length && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 100));
        left = matching(await this.deps.listProcesses()).filter((p) => orphans.includes(p));
      }
      signal(left, "SIGKILL");
    }
    fs.rmSync(this.ledgerPath, { force: true });
    return { reaped: orphans.map((p) => p.pid) };
  }

  /** Write every live server's pids and command lines, read back from the process table. */
  async recordLedger() {
    const live = [...this.entries.values()].filter((e) => this.running(e));
    if (live.length === 0) {
      fs.rmSync(this.ledgerPath, { force: true });
      return;
    }
    const table = await this.deps.listProcesses();
    const byPid = new Map(table.map((r) => [r.pid, r]));
    const servers = live.map((e) => {
      const root = byPid.get(e.child.pid);
      const processes = root ? [{ pid: root.pid, command: root.command }, ...descendantsFromTable(table, root.pid).descendants] : [];
      return { key: e.key, name: e.spec.name, processes };
    });
    fs.mkdirSync(path.dirname(this.ledgerPath), { recursive: true });
    const tmp = `${this.ledgerPath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ appPid: process.pid, servers }));
    fs.renameSync(tmp, this.ledgerPath);
  }

  // ── Internals ─────────────────────────────────────────────────────────

  serial(key, fn) {
    const before = this.queues.get(key) || Promise.resolve();
    const run = before.catch(() => {}).then(fn);
    this.queues.set(key, run);
    return run.finally(() => {
      if (this.queues.get(key) === run) this.queues.delete(key);
    });
  }

  require(key) {
    const entry = this.entries.get(key);
    if (!entry) throw new BridgeError("no_such_server", "GAIA has not started that dev server. Call dev_server(action=\"list\") to see the ones it has.");
    return entry;
  }

  running(entry) {
    return entry.status === "starting" || entry.status === "ready";
  }

  async checkPort(key, spec) {
    if (this.deps.blockedPorts().includes(spec.port)) {
      throw new BridgeError("port_in_use", `Port ${spec.port} is GAIA's own. Pick another "port" in launch.json.`);
    }
    if (!(await portAnswers(spec.port))) return;
    const other = [...this.entries.values()].find((e) => e.key !== key && this.running(e) && e.spec.port === spec.port);
    const who = other ? `GAIA's dev server "${other.spec.name}" already uses it; stop that one first` : "another program is already listening there; stop it, or pick another \"port\" in launch.json";
    throw new BridgeError("port_in_use", `Port ${spec.port} is taken: ${who}.`, { port: spec.port });
  }

  spawn(key, spec, chat, searchPath = null) {
    const log = new CappedLog(path.join(this.deps.dir, `${key}.log`));
    log.write(`[GAIA] ${new Date().toISOString()} starting: ${spec.argv.join(" ")} (in ${spec.cwd})\n`);
    const entry = {
      key,
      spec,
      chats: [chat],
      log,
      logFile: log.file,
      status: "starting",
      startedAt: Date.now(),
      exitCode: null,
      halting: false,
      spawnError: null,
      child: null,
    };
    this.entries.set(key, entry);
    const child = this.deps.spawn(spec.argv[0], spec.argv.slice(1), {
      cwd: spec.cwd,
      env: childEnv(spec, this.deps.env, searchPath),
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    entry.child = child;
    child.stdout.on("data", (c) => log.write(c));
    child.stderr.on("data", (c) => log.write(c));
    child.once("error", (err) => {
      entry.spawnError = err;
      log.write(`[GAIA] could not start ${spec.argv[0]}: ${err.message}\n`);
    });
    child.once("close", (code, sig) => {
      entry.exitCode = code === null ? null : code;
      log.write(`[GAIA] exited ${code === null ? `on ${sig}` : `with code ${code}`}\n`);
      log.close();
      entry.status = entry.halting ? "stopped" : entry.status === "starting" ? "failed" : "exited";
      this.deps.onChange();
      if (!entry.halting) {
        this.recordLedger().catch((err) => this.deps.logger.error(`[dev-servers] Ledger update failed: ${err.message}`));
      }
    });
    this.deps.onChange();
    return entry;
  }

  async untilReady(entry) {
    const deadline = entry.startedAt + entry.spec.readyTimeoutMs;
    for (;;) {
      if (entry.status !== "starting") throw this.failed(entry, "exited before it answered");
      if (await portAnswers(entry.spec.port)) break;
      if (Date.now() > deadline) {
        await this.halt(entry);
        throw this.failed(entry, `did not answer within ${Math.round(entry.spec.readyTimeoutMs / 1000)} s, so GAIA stopped it`);
      }
      await new Promise((r) => setTimeout(r, PROBE_EVERY_MS));
    }
    entry.status = "ready";
    this.deps.onChange();
  }

  failed(entry, what) {
    const { spec } = entry;
    const lines = readLines(entry.logFile).slice(-FAIL_LINES);
    const code = entry.exitCode === null ? "" : ` (exit code ${entry.exitCode})`;
    const cause = entry.spawnError ? ` Could not start ${spec.argv[0]}: ${entry.spawnError.message}.` : "";
    return new BridgeError(
      "dev_server_failed",
      `Dev server "${spec.name}" (${spec.argv.join(" ")}) ${what} on port ${spec.port}${code}.${cause} Its last log lines:\n${lines.join("\n")}`,
      { exitCode: entry.exitCode, lines }
    );
  }

  async halt(entry) {
    entry.halting = true;
    const closed = new Promise((resolve) => {
      if (entry.child.exitCode !== null || entry.child.signalCode !== null) resolve();
      else entry.child.once("close", resolve);
    });
    await this.deps.killTree(entry.child, { label: `dev server "${entry.spec.name}"`, log: this.deps.logger.log, logError: this.deps.logger.error });
    await closed;
    entry.status = "stopped";
    this.deps.onChange();
  }

  /** @returns {DevServerState} */
  stateOf(e) {
    return {
      key: e.key,
      name: e.spec.name,
      argv: e.spec.argv.slice(),
      cwd: e.spec.cwd,
      port: e.spec.port,
      env: { ...e.spec.env },
      url: e.spec.url,
      status: e.status,
      pid: e.child && this.running(e) ? e.child.pid : null,
      startedAt: e.startedAt,
      exitCode: e.exitCode,
      logFile: e.logFile,
      chats: e.chats.slice(),
    };
  }
}

module.exports = { DevServerSupervisor, ENV_NAMES, ENV_PREFIXES, INHERITED_ENV, LOG_CAP_BYTES, CappedLog, childEnv, envNameAllowed, portAnswers };
