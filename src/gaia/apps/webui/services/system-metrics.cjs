// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * GAIA Agent UI — System metrics collection (issue #2007)
 *
 * Collects real host metrics (CPU, memory, disk, network, per-process
 * usage) for the observability dashboard and exposes them to the
 * renderer over the `system:get-metrics` IPC channel. The shape matches
 * the renderer's `SystemMetrics` type (src/types/agent.ts).
 *
 * GPU/NPU utilization is intentionally omitted (left `undefined`): there
 * is no dependency-free cross-platform source for it, and the renderer
 * type marks those fields optional — `undefined` renders as
 * "unavailable", never as a fake zero.
 *
 * Errors propagate: a failing source rejects the IPC invoke so the
 * renderer can surface it. No silent nulls.
 */

"use strict";

const fs = require("fs");
const os = require("os");
const { app, ipcMain } = require("electron");

const SYSTEM_GET_METRICS_CHANNEL = "system:get-metrics";

const BYTES_PER_GB = 1024 * 1024 * 1024;

/** Minimum sampling window for the very first CPU reading (ms). */
const FIRST_CPU_SAMPLE_MS = 120;

// Previous aggregate CPU times — CPU% is a delta between two samples.
let prevCpuTimes = null;

function readCpuTimes() {
  let idle = 0;
  let total = 0;
  for (const cpu of os.cpus()) {
    for (const [kind, ms] of Object.entries(cpu.times)) {
      total += ms;
      if (kind === "idle") idle += ms;
    }
  }
  return { idle, total };
}

function cpuPercentBetween(prev, curr) {
  const totalDelta = curr.total - prev.total;
  const idleDelta = curr.idle - prev.idle;
  if (totalDelta <= 0) return 0;
  const pct = (1 - idleDelta / totalDelta) * 100;
  return Math.min(100, Math.max(0, pct));
}

async function sampleCpuPercent() {
  if (prevCpuTimes === null) {
    // First call: take a short two-point sample so the reading is real,
    // not a since-boot average.
    prevCpuTimes = readCpuTimes();
    await new Promise((r) => setTimeout(r, FIRST_CPU_SAMPLE_MS));
  }
  const curr = readCpuTimes();
  const pct = cpuPercentBetween(prevCpuTimes, curr);
  prevCpuTimes = curr;
  return pct;
}

async function readDiskUsage(diskPath) {
  let stat;
  try {
    stat = await fs.promises.statfs(diskPath);
  } catch (err) {
    throw new Error(
      `Failed to read disk usage for ${diskPath}: ${err.message}. ` +
        "The observability dashboard needs a readable filesystem path — " +
        "check that the path exists and is accessible."
    );
  }
  const totalBytes = stat.blocks * stat.bsize;
  const freeBytes = stat.bfree * stat.bsize;
  return {
    diskUsedGB: (totalBytes - freeBytes) / BYTES_PER_GB,
    diskTotalGB: totalBytes / BYTES_PER_GB,
  };
}

function readProcesses(now) {
  // Electron's own process tree (main, renderer, GPU, utility) — the
  // processes this app can meaningfully report on without a system-wide
  // process scanner dependency.
  return app.getAppMetrics().map((p) => ({
    pid: p.pid,
    name: p.name || p.serviceName || p.type,
    cpuPercent: p.cpu.percentCPUUsage,
    memoryMB: p.memory.workingSetSize / 1024, // workingSetSize is in KB
    uptime: p.creationTime ? Math.max(0, (now - p.creationTime) / 1000) : 0,
  }));
}

function readNetworkUp() {
  return Object.values(os.networkInterfaces()).some((addrs) =>
    (addrs || []).some((a) => !a.internal)
  );
}

/**
 * Collect a full SystemMetrics snapshot from the real host.
 *
 * @param {object} [options]
 * @param {string} [options.diskPath] Filesystem path to report disk usage
 *   for (defaults to the user's home directory).
 * @returns {Promise<object>} SystemMetrics-shaped snapshot.
 */
async function collectSystemMetrics(options = {}) {
  const diskPath = options.diskPath || os.homedir();

  const [cpuPercent, disk] = await Promise.all([
    sampleCpuPercent(),
    readDiskUsage(diskPath),
  ]);

  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  const now = Date.now();

  return {
    cpuPercent,
    memoryUsedGB: (totalMem - freeMem) / BYTES_PER_GB,
    memoryTotalGB: totalMem / BYTES_PER_GB,
    diskUsedGB: disk.diskUsedGB,
    diskTotalGB: disk.diskTotalGB,
    networkUp: readNetworkUp(),
    processes: readProcesses(now),
    timestamp: now,
  };
}

/**
 * Register the `system:get-metrics` IPC handler. Collection errors reject
 * the renderer's invoke() so the store can surface them.
 */
function registerIpcHandlers() {
  ipcMain.handle(SYSTEM_GET_METRICS_CHANNEL, async () => {
    return collectSystemMetrics();
  });
}

module.exports = {
  collectSystemMetrics,
  registerIpcHandlers,
  SYSTEM_GET_METRICS_CHANNEL,
};
