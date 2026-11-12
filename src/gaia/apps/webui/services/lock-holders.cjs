// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * lock-holders.cjs — which processes hold a file open on Windows (#6570).
 *
 * Asks Windows Restart Manager, the API installers use for "files in use",
 * through a PowerShell script, then sorts the holders into GAIA processes the
 * user can let the app close and other programs it must only name.
 */

"use strict";

const childProcess = require("child_process");

const QUERY_TIMEOUT_MS = 30000;
const KILL_TIMEOUT_MS = 10000;
// taskkill's exit code when the process has already exited.
const TASKKILL_NOT_FOUND = 128;

const RM_SCRIPT = String.raw`
$ErrorActionPreference = 'Stop'
$files = [string[]](ConvertFrom-Json $env:GAIA_LOCKED_FILES)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class GaiaRestartManager {
  [StructLayout(LayoutKind.Sequential)]
  struct RM_UNIQUE_PROCESS { public int dwProcessId; public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime; }
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct RM_PROCESS_INFO {
    public RM_UNIQUE_PROCESS Process;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string strAppName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string strServiceShortName;
    public int ApplicationType; public uint AppStatus; public uint TSSessionId;
    [MarshalAs(UnmanagedType.Bool)] public bool bRestartable;
  }
  [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] static extern int RmStartSession(out uint h, int flags, string key);
  [DllImport("rstrtmgr.dll")] static extern int RmEndSession(uint h);
  [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)] static extern int RmRegisterResources(uint h, uint nFiles, string[] files, uint nApps, RM_UNIQUE_PROCESS[] apps, uint nServices, string[] services);
  [DllImport("rstrtmgr.dll")] static extern int RmGetList(uint h, out uint needed, ref uint count, [In, Out] RM_PROCESS_INFO[] info, ref uint reasons);
  public static int[] Holders(string[] files) {
    uint h;
    int rc = RmStartSession(out h, 0, Guid.NewGuid().ToString());
    if (rc != 0) throw new Exception("RmStartSession failed with " + rc);
    try {
      rc = RmRegisterResources(h, (uint)files.Length, files, 0, null, 0, null);
      if (rc != 0) throw new Exception("RmRegisterResources failed with " + rc);
      uint needed = 0, count = 0, reasons = 0;
      rc = RmGetList(h, out needed, ref count, null, ref reasons);
      if (rc == 0) return new int[0];
      if (rc != 234) throw new Exception("RmGetList failed with " + rc);
      RM_PROCESS_INFO[] info = new RM_PROCESS_INFO[needed];
      count = needed;
      rc = RmGetList(h, out needed, ref count, info, ref reasons);
      if (rc != 0) throw new Exception("RmGetList failed with " + rc);
      int[] pids = new int[count];
      for (int i = 0; i < count; i++) pids[i] = info[i].Process.dwProcessId;
      return pids;
    } finally { RmEndSession(h); }
  }
}
'@
function Describe($p) {
  [pscustomobject]@{ pid = [int]$p.ProcessId; name = $p.Name; path = $p.ExecutablePath; commandLine = $p.CommandLine }
}
$holderIds = [GaiaRestartManager]::Holders($files)
$byId = @{}
if ($holderIds.Count -gt 0) {
  foreach ($proc in Get-CimInstance Win32_Process) { $byId[[int]$proc.ProcessId] = $proc }
}
$out = @()
foreach ($holderId in $holderIds) {
  $p = $byId[[int]$holderId]
  if (-not $p) { continue }
  $ancestors = @()
  $child = $p
  while ($ancestors.Count -lt 16) {
    $a = $byId[[int]$child.ParentProcessId]
    # A parent that started after its child is an unrelated process that reused the pid.
    if (-not $a -or $a.ProcessId -eq $child.ProcessId -or $a.CreationDate -gt $child.CreationDate) { break }
    $ancestors += Describe $a
    $child = $a
  }
  $entry = Describe $p
  $entry | Add-Member -NotePropertyName ancestors -NotePropertyValue $ancestors
  $out += $entry
}
ConvertTo-Json -InputObject @($out) -Depth 4 -Compress
`;

function encodedCommand(script) {
  return Buffer.from(script, "utf16le").toString("base64");
}

/**
 * The processes holding any of `files` open, each with its ancestors (parent
 * first): [{ pid, name, path, commandLine, ancestors: [...] }]. Rejects with
 * the script's stderr when Restart Manager or PowerShell fails.
 */
function queryLockHolders(files, { execFile = childProcess.execFile, timeoutMs = QUERY_TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encodedCommand(RM_SCRIPT)],
      {
        env: { ...process.env, GAIA_LOCKED_FILES: JSON.stringify(files) },
        timeout: timeoutMs,
        windowsHide: true,
        maxBuffer: 4 * 1024 * 1024,
      },
      (err, stdout, stderr) => {
        if (err) {
          reject(new Error(`Restart Manager lookup for ${files.join(", ")} failed: ${String(stderr || err.message).trim()}`));
          return;
        }
        try {
          resolve(JSON.parse(String(stdout).trim() || "[]"));
        } catch (parseErr) {
          reject(new Error(`Restart Manager lookup printed something other than JSON: ${String(stdout).slice(0, 200)}`));
        }
      }
    );
  });
}

function winPath(p) {
  return String(p || "").replace(/\//g, "\\");
}

/** A GAIA install's own executable: under a .gaia venv, agents or bin folder, or GAIA's desktop or agent binary. */
function isGaiaExecutable(exePath) {
  const p = winPath(exePath).toLowerCase();
  if (!p) return false;
  if (/\\\.gaia\\(?:venv\\scripts|agents|bin)\\/.test(p)) return true;
  const base = p.split("\\").pop();
  return base === "gaia-desktop.exe" || base === "gaia-agent.exe";
}

/** Where a holder came from, for people: the folder holding its `.gaia`, else its own folder. */
function startedFrom(exePath) {
  const p = winPath(exePath);
  if (!p) return null;
  const at = p.toLowerCase().indexOf("\\.gaia\\");
  if (at > 0) return p.slice(0, at);
  const slash = p.lastIndexOf("\\");
  return slash > 0 ? p.slice(0, slash) : null;
}

/**
 * One entry per program to name: a GAIA holder reports the topmost GAIA
 * process above it (so gaia.exe stands for its python child), anything else
 * reports itself. Holders inside this app's own process tree are never GAIA
 * to close. Returns [{ pid, name, path, folder, isGaia }].
 */
function summarizeHolders(raw, ownPid) {
  const byPid = new Map();
  for (const holder of raw || []) {
    const chain = [holder, ...(holder.ancestors || [])];
    const ownAt = chain.findIndex((p) => p.pid === ownPid);
    const outside = ownAt === -1 ? chain : chain.slice(0, ownAt);
    let root = null;
    for (const p of outside) if (isGaiaExecutable(p.path || p.name)) root = p;
    const shown = root || holder;
    if (byPid.has(shown.pid)) continue;
    byPid.set(shown.pid, {
      pid: shown.pid,
      name: shown.name || null,
      path: shown.path || null,
      folder: startedFrom(shown.path),
      isGaia: Boolean(root),
    });
  }
  return [...byPid.values()];
}

/** End each pid and its children; a pid that has already exited is not an error. */
async function endProcessTrees(pids, { execFile = childProcess.execFile } = {}) {
  for (const pid of pids) {
    await new Promise((resolve, reject) => {
      execFile(
        "taskkill",
        ["/PID", String(pid), "/T", "/F"],
        { timeout: KILL_TIMEOUT_MS, windowsHide: true },
        (err, stdout, stderr) => {
          if (!err || err.code === TASKKILL_NOT_FOUND) resolve();
          else reject(new Error(`Could not end process ${pid}: ${String(stderr || err.message).trim()}`));
        }
      );
    });
  }
}

module.exports = {
  queryLockHolders,
  summarizeHolders,
  endProcessTrees,
  isGaiaExecutable,
  startedFrom,
};
