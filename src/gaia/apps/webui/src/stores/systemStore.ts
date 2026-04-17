// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for system metrics and dashboard state.
 *
 * Tracks CPU, memory, GPU, NPU, and disk usage over time for the
 * system dashboard. Maintains a rolling history of the last 60 entries
 * for sparkline charts and trend visualization.
 */

import { create } from 'zustand';
import type { SystemMetrics, ProcessInfo } from '../types/agent';

// ── Constants ────────────────────────────────────────────────────────────

/** Maximum number of metrics snapshots kept in history for charts. */
const MAX_HISTORY_SIZE = 60;

/** Default polling interval in milliseconds. */
const DEFAULT_POLL_INTERVAL = 2000;

// ── State Interface ──────────────────────────────────────────────────────

interface SystemState {
  /** Current system metrics snapshot (null until first poll). */
  metrics: SystemMetrics | null;
  /** Rolling history of metrics snapshots (last 60 entries for charts). */
  metricsHistory: SystemMetrics[];
  /** Whether the system dashboard panel is visible. */
  showDashboard: boolean;
  /** Whether the metrics polling loop is active. */
  isPolling: boolean;
  /** Polling interval in milliseconds. */
  pollInterval: number;

  // ── Internal ──────────────────────────────────────────────────────────
  /** Timer ID for the polling interval (null when not polling). */
  _timerId: ReturnType<typeof setInterval> | null;

  // ── Metrics Actions ───────────────────────────────────────────────────
  /** Push a new metrics snapshot, update current, and trim history. */
  updateMetrics: (metrics: SystemMetrics) => void;

  // ── UI Actions ────────────────────────────────────────────────────────
  setShowDashboard: (show: boolean) => void;

  // ── Polling Actions ───────────────────────────────────────────────────
  /** Start the polling timer. Calls the fetch callback on each interval. */
  startPolling: () => void;
  /** Stop the polling timer. */
  stopPolling: () => void;
  /** Update the polling interval (restarts polling if currently active). */
  setPollInterval: (ms: number) => void;
}

// ── Polling Fetch Helper ─────────────────────────────────────────────────

/**
 * Fetch system metrics from the Electron main process.
 * Returns null if the Electron API is not available.
 */
async function fetchSystemMetrics(): Promise<SystemMetrics | null> {
  // TODO: Implement IPC call to main process for system metrics.
  // For now this is a placeholder — the main process will expose a
  // system:getMetrics IPC handler in a future ticket.
  console.log('[systemStore] fetchSystemMetrics: not yet implemented (needs IPC)');
  return null;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const useSystemStore = create<SystemState>((set, get) => ({
  // State
  metrics: null,
  metricsHistory: [],
  showDashboard: false,
  isPolling: false,
  pollInterval: DEFAULT_POLL_INTERVAL,
  _timerId: null,

  // ── Metrics Actions ───────────────────────────────────────────────────

  updateMetrics: (metrics) =>
    set((state) => {
      const history = [...state.metricsHistory, metrics];
      // Trim to the last MAX_HISTORY_SIZE entries
      const trimmed = history.length > MAX_HISTORY_SIZE
        ? history.slice(history.length - MAX_HISTORY_SIZE)
        : history;
      return {
        metrics,
        metricsHistory: trimmed,
      };
    }),

  // ── UI Actions ────────────────────────────────────────────────────────

  setShowDashboard: (show) => set({ showDashboard: show }),

  // ── Polling Actions ───────────────────────────────────────────────────

  startPolling: () => {
    const { isPolling, pollInterval, _timerId } = get();
    if (isPolling && _timerId !== null) return; // Already polling

    const tick = async () => {
      const result = await fetchSystemMetrics();
      if (result) {
        get().updateMetrics(result);
      }
    };

    // Fire immediately, then on interval
    tick();
    const timerId = setInterval(tick, pollInterval);
    set({ isPolling: true, _timerId: timerId });
  },

  stopPolling: () => {
    const { _timerId } = get();
    if (_timerId !== null) {
      clearInterval(_timerId);
    }
    set({ isPolling: false, _timerId: null });
  },

  setPollInterval: (ms) => {
    const { isPolling } = get();
    set({ pollInterval: ms });
    if (isPolling) {
      // Restart polling with the new interval
      get().stopPolling();
      get().startPolling();
    }
  },
}));

// ── Selectors ────────────────────────────────────────────────────────────

/** Extract CPU usage percentage history for sparkline charts. */
export const selectCpuHistory = (state: SystemState): number[] =>
  state.metricsHistory.map((m) => m.cpuPercent);

/** Extract memory usage (GB) history for sparkline charts. */
export const selectMemoryHistory = (state: SystemState): number[] =>
  state.metricsHistory.map((m) => m.memoryUsedGB);

/** Check whether GPU metrics are available (i.e., a discrete/integrated GPU was detected). */
export const selectGpuAvailable = (state: SystemState): boolean =>
  state.metrics?.gpuPercent !== undefined;
