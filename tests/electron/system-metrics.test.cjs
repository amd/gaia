// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Real system-metrics collection for the observability dashboard — issue #2007.
 *
 * collectSystemMetrics() must return REAL host data (CPU, memory, disk,
 * network, processes) in the SystemMetrics shape the renderer store expects,
 * and must throw — not return null — when a source is unavailable.
 */

"use strict";

const fs = require("fs");
const path = require("path");

const { app, ipcMain } = require("electron"); // mocked via moduleNameMapper
const {
  collectSystemMetrics,
  registerIpcHandlers,
  SYSTEM_GET_METRICS_CHANNEL,
} = require("../../src/gaia/apps/webui/services/system-metrics.cjs");

const PRELOAD_PATH = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/preload.cjs"
);

describe("collectSystemMetrics returns real host data", () => {
  test("resolves a fully-populated SystemMetrics snapshot", async () => {
    const before = Date.now();
    const metrics = await collectSystemMetrics();

    // CPU
    expect(typeof metrics.cpuPercent).toBe("number");
    expect(metrics.cpuPercent).toBeGreaterThanOrEqual(0);
    expect(metrics.cpuPercent).toBeLessThanOrEqual(100);

    // Memory — a real host always has non-zero totals and usage.
    expect(metrics.memoryTotalGB).toBeGreaterThan(0);
    expect(metrics.memoryUsedGB).toBeGreaterThan(0);
    expect(metrics.memoryUsedGB).toBeLessThanOrEqual(metrics.memoryTotalGB);

    // Disk — statfs on the home dir must yield real capacity.
    expect(metrics.diskTotalGB).toBeGreaterThan(0);
    expect(metrics.diskUsedGB).toBeGreaterThanOrEqual(0);
    expect(metrics.diskUsedGB).toBeLessThanOrEqual(metrics.diskTotalGB);

    // Network + timestamp
    expect(typeof metrics.networkUp).toBe("boolean");
    expect(metrics.timestamp).toBeGreaterThanOrEqual(before);

    // Processes come from Electron's app.getAppMetrics()
    expect(Array.isArray(metrics.processes)).toBe(true);
    expect(metrics.processes.length).toBeGreaterThan(0);
    for (const p of metrics.processes) {
      expect(typeof p.pid).toBe("number");
      expect(typeof p.name).toBe("string");
      expect(p.name).not.toBe("");
      expect(typeof p.cpuPercent).toBe("number");
      expect(p.memoryMB).toBeGreaterThan(0);
      expect(p.uptime).toBeGreaterThanOrEqual(0);
    }
  });

  test("consecutive calls both return real values (cpu delta path)", async () => {
    const first = await collectSystemMetrics();
    const second = await collectSystemMetrics();
    expect(first.cpuPercent).toBeGreaterThanOrEqual(0);
    expect(second.cpuPercent).toBeGreaterThanOrEqual(0);
    expect(second.timestamp).toBeGreaterThanOrEqual(first.timestamp);
  });

  test("throws an actionable error when the disk source is unavailable — no silent null", async () => {
    await expect(
      collectSystemMetrics({ diskPath: "/nonexistent-gaia-metrics-path" })
    ).rejects.toThrow(/\/nonexistent-gaia-metrics-path/);
  });
});

describe("IPC wiring for system:get-metrics", () => {
  test("registerIpcHandlers exposes the channel and it returns metrics", async () => {
    registerIpcHandlers();
    const metrics = await ipcMain.simulateInvoke(SYSTEM_GET_METRICS_CHANNEL);
    expect(metrics.memoryTotalGB).toBeGreaterThan(0);
    ipcMain.removeHandler(SYSTEM_GET_METRICS_CHANNEL);
  });

  test("preload.cjs invokes the same channel the service registers", () => {
    const preloadSrc = fs.readFileSync(PRELOAD_PATH, "utf8");
    expect(preloadSrc).toContain(
      `ipcRenderer.invoke("${SYSTEM_GET_METRICS_CHANNEL}")`
    );
  });
});
