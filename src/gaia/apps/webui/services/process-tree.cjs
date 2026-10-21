// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Stop a child process together with everything it started.
 *
 * Killing only the direct child leaves its own children running after the app
 * is gone: the uv `gaia.exe` launcher's Python, a PyInstaller bootloader's
 * server, or the MCP servers and shell commands an agent spawned. They keep
 * ports and file handles, and the next launch trips over them.
 *
 * The tree is read from the OS process table (`ps` on Linux and macOS,
 * Win32_Process on Windows), walked by parent pid, and every member is
 * signalled. GAIA's shared servers (the daemon and Lemonade) are left alone:
 * every GAIA client shares them, they outlive the app on purpose, and the
 * daemon unloads its models itself once the app is gone (GAIA_APP_PID).
 */

"use strict";

const childProcess = require("child_process");

const DEFAULT_GRACE_MS = 3000;
const DEFAULT_FORCE_WAIT_MS = 1000;
const LIST_TIMEOUT_MS = 5000;
const POLL_MS = 50;

// The daemon by its module or CLI verb; Lemonade by its executable's name,
// so a tool that merely mentions Lemonade (--lemonade-url) is still stopped.
const SHARED_SERVER_PATTERN =
  /(?:-m\s+gaia\.daemon\b)|(?:\bgaia(?:\.exe)?["']?\s+daemon\b)|(?:^|[\\/"'\s])(?:lemonade|lemonade-server|lemonadeserver|lemonade-tray|lemond)(?:\.exe)?(?=["'\s]|$)/i;

/** True for a command line that belongs to a server shared by every GAIA client. */
function isSharedServer(command) {
  return SHARED_SERVER_PATTERN.test(command || "");
}

// Linux and macOS re-parent an orphan, so its ppid is always current.
function parsePosixTable(stdout) {
  const rows = [];
  for (const line of stdout.split(/\r?\n/)) {
    const m = /^\s*(\d+)\s+(\d+)\s?(.*)$/.exec(line);
    if (m) rows.push({ pid: Number(m[1]), ppid: Number(m[2]), command: m[3].trim(), started: null });
  }
  return rows;
}

// Windows never updates ParentProcessId when the parent dies, and reuses
// pids, so each row carries its creation time (FILETIME) for the walk to
// reject a "child" older than the parent it claims.
function parseWindowsTable(stdout) {
  const rows = [];
  for (const line of stdout.split(/\r?\n/)) {
    const parts = line.split("\t");
    if (parts.length < 3 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1])) continue;
    rows.push({
      pid: Number(parts[0]),
      ppid: Number(parts[1]),
      started: /^\d+$/.test(parts[2]) ? BigInt(parts[2]) : null,
      command: parts.slice(3).join("\t").trim(),
    });
  }
  return rows;
}

/**
 * Read the OS process table as `[{ pid, ppid, command }]`.
 * Rejects with what failed and which command ran; never returns an empty
 * table in place of an error.
 */
function listProcesses({ platform = process.platform, execFile = childProcess.execFile } = {}) {
  const [cmd, args, parse] =
    platform === "win32"
      ? [
          "powershell.exe",
          [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$(if ($_.CreationDate) { $_.CreationDate.ToFileTimeUtc() })`t$($_.CommandLine)\" }",
          ],
          parseWindowsTable,
        ]
      : ["ps", ["-A", "-ww", "-o", "pid=", "-o", "ppid=", "-o", "command="], parsePosixTable];
  return new Promise((resolve, reject) => {
    execFile(
      cmd,
      args,
      { windowsHide: true, timeout: LIST_TIMEOUT_MS, maxBuffer: 32 * 1024 * 1024 },
      (err, stdout) => {
        if (err) {
          reject(new Error(`could not read the process table with ${cmd}: ${err.message}`, { cause: err }));
          return;
        }
        resolve(parse(String(stdout)));
      }
    );
  });
}

/**
 * Every descendant of `rootPid`, parents before children, skipping shared
 * servers and anything they started.
 * @returns {{ descendants: {pid:number, command:string}[], skipped: {pid:number, command:string}[] }}
 */
function descendantsFromTable(table, rootPid) {
  const byPid = new Map(table.map((r) => [r.pid, r]));
  const children = new Map();
  for (const row of table) {
    if (row.pid === row.ppid) continue;
    if (!children.has(row.ppid)) children.set(row.ppid, []);
    children.get(row.ppid).push(row);
  }
  const descendants = [];
  const skipped = [];
  const seen = new Set([rootPid]);
  const queue = [rootPid];
  while (queue.length) {
    const parent = queue.shift();
    const parentRow = byPid.get(parent);
    for (const row of children.get(parent) || []) {
      if (seen.has(row.pid)) continue;
      // Older than its "parent": the real parent died and its pid was reused.
      if (parentRow && parentRow.started != null && row.started != null && row.started < parentRow.started) {
        continue;
      }
      seen.add(row.pid);
      if (isSharedServer(row.command)) {
        skipped.push({ pid: row.pid, command: row.command });
        continue;
      }
      descendants.push({ pid: row.pid, command: row.command });
      queue.push(row.pid);
    }
  }
  return { descendants, skipped };
}

/** Snapshot the descendants of a live process (see descendantsFromTable). */
async function listDescendants(rootPid, opts = {}) {
  const table = await (opts.listProcesses || listProcesses)(opts);
  return descendantsFromTable(table, rootPid);
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // EPERM: it exists but belongs to someone else.
    return err.code === "EPERM";
  }
}

function rootAlive(proc, pid) {
  if (proc) return proc.exitCode === null && proc.signalCode === null;
  return pidAlive(pid);
}

/**
 * Signal the root and every descendant, wait up to `graceMs`, then SIGKILL
 * whatever is left — including anything the tree started in the meantime —
 * and wait up to `forceWaitMs` for it to go.
 *
 * On Windows every signal is TerminateProcess, so the grace period only
 * matters for processes that are already exiting.
 *
 * @param {import('child_process').ChildProcess|number} target
 *   Prefer the ChildProcess: its exitCode is set before the pid can be
 *   reused, so a finished child is never signalled.
 * @param {object} [opts]
 * @param {{pid:number, command:string}[]} [opts.knownDescendants]
 *   Descendants snapshotted earlier, while the root was still alive. Needed
 *   on Linux and macOS when the root may already have exited: its children
 *   are then re-parented and can no longer be found from it. Each is killed
 *   only while its command line still matches, so a reused pid is spared.
 * @returns {Promise<{ killed: number[], survivors: number[] }>}
 */
async function killProcessTree(target, opts = {}) {
  const {
    graceMs = DEFAULT_GRACE_MS,
    forceWaitMs = DEFAULT_FORCE_WAIT_MS,
    knownDescendants = [],
    log = console.log,
    logError = console.error,
    label = "process",
    platform = process.platform,
  } = opts;
  const proc = target && typeof target === "object" ? target : null;
  const pid = proc ? proc.pid : target;
  if (!pid) return { killed: [], survivors: [] };

  const readTable = () => (opts.listProcesses || listProcesses)(opts);

  // pid -> command, for every non-root member we intend to stop.
  const members = new Map();
  const absorb = (table) => {
    const live = new Map(table.map((r) => [r.pid, r.command]));
    // A member that exited, or whose pid now runs something else, is no
    // longer ours to signal.
    for (const [memberPid, command] of [...members]) {
      if (live.get(memberPid) !== command) members.delete(memberPid);
    }
    for (const d of knownDescendants) {
      if (live.has(d.pid) && live.get(d.pid) === d.command) members.set(d.pid, d.command);
    }
    if (live.has(pid) && rootAlive(proc, pid)) {
      for (const d of descendantsFromTable(table, pid).descendants) members.set(d.pid, d.command);
    }
    // Children of members that outlived their parent's handle.
    for (const memberPid of [...members.keys()]) {
      for (const d of descendantsFromTable(table, memberPid).descendants) members.set(d.pid, d.command);
    }
  };

  let tableError = null;
  try {
    absorb(await readTable());
  } catch (err) {
    tableError = err;
    logError(
      `[process-tree] ${label} ${pid}: ${err.message} — only the ${label} itself will be stopped; ` +
        "any process it started may keep running. Check them with Task Manager or `ps`."
    );
  }

  const signal = (p, sig) => {
    try {
      if (p === pid && proc) {
        if (rootAlive(proc, pid)) proc.kill(sig);
      } else {
        process.kill(p, sig);
      }
      return true;
    } catch (err) {
      if (err.code !== "ESRCH") {
        logError(`[process-tree] ${sig} to pid ${p} (${label} ${pid}) failed: ${err.message}`);
      }
      return false;
    }
  };
  const alive = (p) => (p === pid ? rootAlive(proc, pid) : pidAlive(p));
  const waitAllGone = async (pids, ms) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (!pids.some(alive)) return true;
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
    return !pids.some(alive);
  };

  const all = () => [pid, ...members.keys()];
  const killed = [];
  for (const p of all()) {
    if (alive(p) && signal(p, "SIGTERM")) killed.push(p);
  }
  if (killed.length === 0) return { killed, survivors: [] };
  log(`[process-tree] SIGTERM sent to ${label} ${pid} and ${killed.length - (killed.includes(pid) ? 1 : 0)} descendant(s)`);

  if (await waitAllGone(all(), graceMs)) return { killed, survivors: [] };

  if (platform === "win32") {
    // SIGTERM was already TerminateProcess, so a member still listed is one
    // that could not be ended or a pid already reused: only the root, through
    // its handle, is worth a second try. Skipping the re-read also keeps a
    // slow PowerShell from eating the quit's time limit.
    members.clear();
  } else if (!tableError) {
    try {
      absorb(await readTable());
    } catch (err) {
      // Without a fresh table a member's pid may already belong to a stranger.
      members.clear();
      logError(
        `[process-tree] ${label} ${pid}: re-reading the tree before SIGKILL failed (${err.message}); ` +
          `only the ${label} itself gets SIGKILL — any process it started may keep running.`
      );
    }
  }
  const stubborn = all().filter(alive);
  for (const p of stubborn) {
    if (signal(p, "SIGKILL") && !killed.includes(p)) killed.push(p);
  }
  log(`[process-tree] SIGKILL sent to ${stubborn.length} process(es) of ${label} ${pid} that ignored SIGTERM`);

  await waitAllGone(stubborn, forceWaitMs);
  const survivors = stubborn.filter(alive);
  if (survivors.length) {
    logError(
      `[process-tree] ${label} ${pid}: pid(s) ${survivors.join(", ")} still running ${forceWaitMs} ms after SIGKILL — ` +
        "end them with Task Manager or `kill -9` before relaunching GAIA."
    );
  }
  return { killed, survivors };
}

module.exports = {
  killProcessTree,
  listDescendants,
  listProcesses,
  descendantsFromTable,
  isSharedServer,
  parsePosixTable,
  parseWindowsTable,
};
