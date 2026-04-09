// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for pipeline metrics dashboard.
 *
 * Handles metrics polling, auto-refresh, and historical data.
 */

import { create } from 'zustand';
import type {
  PipelineMetricsResponse,
  PipelineMetricsHistory,
  PipelineAggregateMetrics,
} from '../../types';
import * as api from '../../services/api';
import { log } from '../../utils/logger';

// ── Constants ────────────────────────────────────────────────────────

/** Default polling interval in milliseconds. */
const DEFAULT_POLL_INTERVAL = 5000;

/** Maximum number of history snapshots kept for charts. */
const MAX_HISTORY_SIZE = 60;

// ── State Interface ──────────────────────────────────────────────────

interface MetricsState {
  // State
  /** Current pipeline metrics (null until loaded) */
  currentMetrics: PipelineMetricsResponse | null;
  /** Selected pipeline ID for detailed view */
  selectedPipelineId: string | null;
  /** Metrics history for charts */
  metricsHistory: PipelineMetricsHistory[];
  /** Aggregate metrics across all pipelines */
  aggregateMetrics: PipelineAggregateMetrics | null;
  /** Auto-refresh enabled */
  autoRefresh: boolean;
  /** Polling interval in milliseconds */
  pollInterval: number;
  /** Whether metrics are being loaded */
  isLoading: boolean;
  /** Error message from last failed operation */
  lastError: string | null;

  // Internal
  /** Timer ID for polling loop */
  _timerId: ReturnType<typeof setInterval> | null;

  // Actions - State setters
  /** Set current metrics */
  setCurrentMetrics: (metrics: PipelineMetricsResponse | null) => void;
  /** Set selected pipeline ID */
  setSelectedPipelineId: (id: string | null) => void;
  /** Add history snapshot */
  addHistorySnapshot: (history: PipelineMetricsHistory) => void;
  /** Set aggregate metrics */
  setAggregateMetrics: (metrics: PipelineAggregateMetrics | null) => void;
  /** Set auto-refresh toggle */
  setAutoRefresh: (enabled: boolean) => void;
  /** Set poll interval */
  setPollInterval: (ms: number) => void;
  /** Set loading state */
  setIsLoading: (loading: boolean) => void;
  /** Set last error */
  setLastError: (error: string | null) => void;

  // Actions - Data fetching
  /** Fetch metrics for a specific pipeline */
  fetchPipelineMetrics: (pipelineId: string) => Promise<void>;
  /** Fetch metrics history */
  fetchMetricsHistory: (pipelineId: string, metricType?: string) => Promise<void>;
  /** Fetch aggregate metrics */
  fetchAggregateMetrics: () => Promise<void>;
  /** Start polling */
  startPolling: () => void;
  /** Stop polling */
  stopPolling: () => void;
  /** Refresh all metrics */
  refreshAll: () => Promise<void>;
}

// ── Store Implementation ─────────────────────────────────────────────

export const useMetricsStore = create<MetricsState>((set, get) => ({
  // Initial state
  currentMetrics: null,
  selectedPipelineId: null,
  metricsHistory: [],
  aggregateMetrics: null,
  autoRefresh: true,
  pollInterval: DEFAULT_POLL_INTERVAL,
  isLoading: false,
  lastError: null,
  _timerId: null,

  // State setters
  setCurrentMetrics: (metrics) => set({ currentMetrics: metrics }),
  setSelectedPipelineId: (id) => set({ selectedPipelineId: id }),
  addHistorySnapshot: (history) =>
    set((state) => {
      const newHistory = [...state.metricsHistory, history];
      if (newHistory.length > MAX_HISTORY_SIZE) {
        newHistory.shift();
      }
      return { metricsHistory: newHistory };
    }),
  setAggregateMetrics: (metrics) => set({ aggregateMetrics: metrics }),
  setAutoRefresh: (enabled) => {
    set({ autoRefresh: enabled });
    if (!enabled) {
      get().stopPolling();
    } else if (get().selectedPipelineId) {
      get().startPolling();
    }
  },
  setPollInterval: (ms) => {
    const { _timerId } = get();
    set({ pollInterval: ms });
    if (_timerId !== null) {
      get().stopPolling();
      get().startPolling();
    }
  },
  setIsLoading: (loading) => set({ isLoading: loading }),
  setLastError: (error) => set({ lastError: error }),

  // Data fetching
  fetchPipelineMetrics: async (pipelineId) => {
    set({ isLoading: true, lastError: null, selectedPipelineId: pipelineId });
    try {
      const metrics = await api.getPipelineMetrics(pipelineId);
      set({ currentMetrics: metrics, isLoading: false });
      log.ui.info(`[metricsStore] Fetched metrics for pipeline: ${pipelineId}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      set({ lastError: `Failed to fetch metrics: ${message}`, isLoading: false });
      log.ui.error('[metricsStore] Failed to fetch pipeline metrics:', err);
    }
  },

  fetchMetricsHistory: async (pipelineId, metricType) => {
    try {
      const history = await api.getMetricsHistory(pipelineId, metricType);
      get().addHistorySnapshot(history);
      log.ui.info(`[metricsStore] Fetched history for pipeline: ${pipelineId}`);
    } catch (err) {
      log.ui.error('[metricsStore] Failed to fetch metrics history:', err);
    }
  },

  fetchAggregateMetrics: async () => {
    try {
      const metrics = await api.getAggregateMetrics();
      set({ aggregateMetrics: metrics });
      log.ui.info('[metricsStore] Fetched aggregate metrics');
    } catch (err) {
      log.ui.error('[metricsStore] Failed to fetch aggregate metrics:', err);
    }
  },

  startPolling: () => {
    const { _timerId, selectedPipelineId, pollInterval } = get();
    if (_timerId !== null || !selectedPipelineId) return;

    const tick = async () => {
      if (selectedPipelineId) {
        await get().fetchPipelineMetrics(selectedPipelineId);
      }
    };

    // Fire immediately, then on interval
    tick();
    const timerId = setInterval(tick, pollInterval);
    set({ _timerId: timerId });
    log.ui.info(`[metricsStore] Started polling (interval: ${pollInterval}ms)`);
  },

  stopPolling: () => {
    const { _timerId } = get();
    if (_timerId !== null) {
      clearInterval(_timerId);
      set({ _timerId: null });
      log.ui.info('[metricsStore] Stopped polling');
    }
  },

  refreshAll: async () => {
    const { selectedPipelineId } = get();
    await Promise.all([
      selectedPipelineId ? get().fetchPipelineMetrics(selectedPipelineId) : Promise.resolve(),
      get().fetchAggregateMetrics(),
    ]);
  },
}));

// ── Selectors ────────────────────────────────────────────────────────

/** Get summary metrics from current pipeline. */
export const selectSummary = (state: MetricsState) => state.currentMetrics?.summary;

/** Get phase timing breakdown. */
export const selectPhaseBreakdown = (state: MetricsState) =>
  state.currentMetrics?.phase_breakdown || {};

/** Get loop metrics. */
export const selectLoopMetrics = (state: MetricsState) =>
  state.currentMetrics?.loop_metrics || {};

/** Get state transitions. */
export const selectStateTransitions = (state: MetricsState) =>
  state.currentMetrics?.state_transitions || [];

/** Get agent selections. */
export const selectAgentSelections = (state: MetricsState) =>
  state.currentMetrics?.agent_selections || [];

/** Get defects by type. */
export const selectDefectsByType = (state: MetricsState) =>
  state.currentMetrics?.defects_by_type || {};

/** Get total defects count. */
export const selectTotalDefects = (state: MetricsState): number => {
  const defects = state.currentMetrics?.defects_by_type || {};
  return Object.values(defects).reduce((sum, count) => sum + count, 0);
};

/** Get quality score average. */
export const selectAverageQuality = (state: MetricsState): number =>
  state.currentMetrics?.summary?.avg_quality_score || 0;

/** Get tokens per second average. */
export const selectAverageTPS = (state: MetricsState): number =>
  state.currentMetrics?.summary?.avg_tps || 0;

/** Get time to first token average. */
export const selectAverageTTFT = (state: MetricsState): number =>
  state.currentMetrics?.summary?.avg_ttft || 0;
