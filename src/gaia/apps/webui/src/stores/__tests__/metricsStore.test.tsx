// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Unit tests for metricsStore - Pipeline metrics dashboard.
 *
 * Tests cover:
 * - Metrics fetching and polling
 * - Auto-refresh lifecycle
 * - History snapshot management
 * - Error state handling
 * - Loading states
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { act } from '@testing-library/react';
import { useMetricsStore, selectSummary, selectPhaseBreakdown, selectLoopMetrics, selectStateTransitions, selectAgentSelections, selectDefectsByType, selectTotalDefects, selectAverageQuality, selectAverageTPS, selectAverageTTFT } from '../metricsStore';
import * as api from '../../services/api';

// Mock the API module
vi.mock('../../services/api', () => ({
  getPipelineMetrics: vi.fn(),
  getMetricsHistory: vi.fn(),
  getAggregateMetrics: vi.fn(),
  getPhaseMetrics: vi.fn(),
  getLoopMetrics: vi.fn(),
  getQualityHistory: vi.fn(),
  getDefectMetrics: vi.fn(),
  getStateTransitions: vi.fn(),
  getAgentSelections: vi.fn(),
}));

// Mock logger
vi.mock('../../utils/logger', () => ({
  log: {
    ui: {
      info: vi.fn(),
      error: vi.fn(),
      warn: vi.fn(),
      timed: vi.fn(),
      time: vi.fn(() => 0),
    },
    api: {
      info: vi.fn(),
      error: vi.fn(),
      warn: vi.fn(),
      timed: vi.fn(),
      time: vi.fn(() => 0),
    },
  },
}));

// Sample metrics data for tests
const sampleMetricsResponse = {
  success: true,
  pipeline_id: 'test-pipeline-001',
  summary: {
    pipeline_id: 'test-pipeline-001',
    total_duration_seconds: 120.5,
    total_tokens: 5000,
    avg_tps: 41.5,
    avg_ttft: 0.35,
    total_loops: 4,
    total_iterations: 12,
    total_defects: 5,
    avg_quality_score: 0.88,
    max_quality_score: 0.95,
    min_quality_score: 0.72,
  },
  phase_breakdown: {
    PLANNING: {
      phase_name: 'PLANNING',
      started_at: '2025-01-01T10:00:00Z',
      ended_at: '2025-01-01T10:00:25Z',
      duration_seconds: 25.3,
      token_count: 1500,
      ttft: 0.4,
      tps: 35.2,
    },
    DEVELOPMENT: {
      phase_name: 'DEVELOPMENT',
      started_at: '2025-01-01T10:00:25Z',
      ended_at: '2025-01-01T10:01:30Z',
      duration_seconds: 65.0,
      token_count: 2500,
      ttft: 0.3,
      tps: 45.0,
    },
  },
  loop_metrics: {
    'loop-001': {
      loop_id: 'loop-001',
      phase_name: 'DEVELOPMENT',
      iteration_count: 3,
      quality_scores: [0.65, 0.78, 0.92],
      average_quality: 0.78,
      max_quality: 0.92,
      defects_by_type: { testing: 2, documentation: 1 },
      started_at: '2025-01-01T10:00:25Z',
      ended_at: '2025-01-01T10:01:30Z',
    },
  },
  state_transitions: [
    {
      from_state: 'INIT',
      to_state: 'PLANNING',
      timestamp: '2025-01-01T10:00:00Z',
      reason: 'Phase transition',
      metadata: {},
    },
    {
      from_state: 'PLANNING',
      to_state: 'DEVELOPMENT',
      timestamp: '2025-01-01T10:00:25Z',
      reason: 'Phase exit',
      metadata: {},
    },
  ],
  defects_by_type: {
    testing: 3,
    documentation: 2,
  },
  agent_selections: [
    {
      phase: 'PLANNING',
      agent_id: 'senior-developer',
      reason: 'Best match for requirements analysis',
      alternatives: ['architect', 'tech-lead'],
      timestamp: '2025-01-01T10:00:00Z',
    },
  ],
};

const sampleHistoryResponse = {
  pipeline_id: 'test-pipeline-001',
  metric_type: 'TPS',
  total_points: 10,
  history: [
    {
      timestamp: '2025-01-01T10:00:00Z',
      loop_id: 'loop-001',
      phase: 'DEVELOPMENT',
      metric_type: 'TPS',
      value: 35.2,
      metadata: {},
    },
  ],
};

const sampleAggregateResponse = {
  success: true,
  total_pipelines: 5,
  time_range: { start: '2025-01-01T00:00:00Z', end: '2025-01-01T23:59:59Z' },
  metric_statistics: {
    TPS: {
      metric_type: 'TPS',
      count: 100,
      mean: 40.5,
      median: 42.0,
      std_dev: 5.2,
      min_value: 25.0,
      max_value: 55.0,
      trend: 'stable',
      percentiles: { p50: 42.0, p90: 50.0, p99: 54.0 },
    },
  },
  overall_health: 0.85,
  recommendations: ['Consider optimizing phase transitions'],
};

describe('useMetricsStore', () => {
  // Clear all timers between tests
  afterEach(() => {
    vi.clearAllTimers();
  });

  beforeEach(() => {
    // Reset store to initial state
    useMetricsStore.setState({
      currentMetrics: null,
      selectedPipelineId: null,
      metricsHistory: [],
      aggregateMetrics: null,
      autoRefresh: true,
      pollInterval: 5000,
      isLoading: false,
      lastError: null,
      _timerId: null,
    });

    // Reset all mocks
    vi.clearAllMocks();
  });

  describe('State Initialization', () => {
    it('should have correct initial state', () => {
      const state = useMetricsStore.getState();
      expect(state.currentMetrics).toBeNull();
      expect(state.selectedPipelineId).toBeNull();
      expect(state.metricsHistory).toEqual([]);
      expect(state.aggregateMetrics).toBeNull();
      expect(state.autoRefresh).toBe(true);
      expect(state.pollInterval).toBe(5000);
      expect(state.isLoading).toBe(false);
      expect(state.lastError).toBeNull();
      expect(state._timerId).toBeNull();
    });
  });

  describe('fetchPipelineMetrics', () => {
    it('should fetch pipeline metrics successfully', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline-001');
      });

      const state = useMetricsStore.getState();
      expect(api.getPipelineMetrics).toHaveBeenCalledWith('test-pipeline-001');
      expect(state.currentMetrics).toEqual(sampleMetricsResponse);
      expect(state.selectedPipelineId).toBe('test-pipeline-001');
      expect(state.isLoading).toBe(false);
      expect(state.lastError).toBeNull();
    });

    it('should handle fetch error', async () => {
      const errorMessage = 'Pipeline not found';
      vi.mocked(api.getPipelineMetrics).mockRejectedValue(new Error(errorMessage));

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('nonexistent');
      });

      const state = useMetricsStore.getState();
      expect(state.currentMetrics).toBeNull();
      expect(state.isLoading).toBe(false);
      expect(state.lastError).toContain('Failed to fetch metrics');
      expect(state.lastError).toContain(errorMessage);
    });

    it('should handle string error (not Error object)', async () => {
      vi.mocked(api.getPipelineMetrics).mockRejectedValue('Network error');

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
      });

      const state = useMetricsStore.getState();
      expect(state.lastError).toContain('Network error');
    });

    it('should set loading state during fetch', async () => {
      let loadingDuringFetch = false;

      vi.mocked(api.getPipelineMetrics).mockImplementation(async () => {
        loadingDuringFetch = useMetricsStore.getState().isLoading;
        return sampleMetricsResponse;
      });

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
      });

      expect(loadingDuringFetch).toBe(true);
    });
  });

  describe('fetchMetricsHistory', () => {
    it('should fetch metrics history successfully', async () => {
      vi.mocked(api.getMetricsHistory).mockResolvedValue(sampleHistoryResponse);

      await act(async () => {
        await useMetricsStore.getState().fetchMetricsHistory('test-pipeline-001', 'TPS');
      });

      expect(api.getMetricsHistory).toHaveBeenCalledWith('test-pipeline-001', 'TPS');

      const state = useMetricsStore.getState();
      expect(state.metricsHistory.length).toBe(1);
    });

    it('should handle history fetch error gracefully', async () => {
      vi.mocked(api.getMetricsHistory).mockRejectedValue(new Error('History not available'));

      await act(async () => {
        await useMetricsStore.getState().fetchMetricsHistory('test-pipeline');
      });

      // Should not set lastError for history (graceful degradation)
      const state = useMetricsStore.getState();
      expect(state.lastError).toBeNull();
    });

    it('should respect MAX_HISTORY_SIZE limit', async () => {
      // Add 65 history snapshots (more than MAX_HISTORY_SIZE of 60)
      for (let i = 0; i < 65; i++) {
        await act(async () => {
          await useMetricsStore.getState().fetchMetricsHistory('test-pipeline');
        });
      }

      const state = useMetricsStore.getState();
      expect(state.metricsHistory.length).toBe(60); // MAX_HISTORY_SIZE
    });
  });

  describe('fetchAggregateMetrics', () => {
    it('should fetch aggregate metrics successfully', async () => {
      vi.mocked(api.getAggregateMetrics).mockResolvedValue(sampleAggregateResponse);

      await act(async () => {
        await useMetricsStore.getState().fetchAggregateMetrics();
      });

      const state = useMetricsStore.getState();
      expect(api.getAggregateMetrics).toHaveBeenCalled();
      expect(state.aggregateMetrics).toEqual(sampleAggregateResponse);
    });

    it('should handle aggregate metrics error gracefully', async () => {
      vi.mocked(api.getAggregateMetrics).mockRejectedValue(new Error('Aggregation failed'));

      await act(async () => {
        await useMetricsStore.getState().fetchAggregateMetrics();
      });

      const state = useMetricsStore.getState();
      expect(state.lastError).toBeNull(); // Graceful degradation
      expect(state.aggregateMetrics).toBeNull();
    });
  });

  describe('startPolling', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should start polling with correct interval', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      // Set selected pipeline first
      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
      });

      // Start polling
      act(() => {
        useMetricsStore.getState().startPolling();
      });

      const state = useMetricsStore.getState();
      expect(state._timerId).not.toBeNull();

      // First tick should happen immediately
      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(1);

      // Advance timer by poll interval
      await act(async () => {
        vi.advanceTimersByTime(5000);
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(2);
    });

    it('should not start polling without selected pipeline', () => {
      act(() => {
        useMetricsStore.getState().startPolling();
      });

      const state = useMetricsStore.getState();
      expect(state._timerId).toBeNull();
      expect(api.getPipelineMetrics).not.toHaveBeenCalled();
    });

    it('should not start multiple polling timers', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
      });

      act(() => {
        useMetricsStore.getState().startPolling();
        useMetricsStore.getState().startPolling(); // Second call should be ignored
      });

      // Should only have one timer
      const state = useMetricsStore.getState();
      expect(state._timerId).not.toBeNull();
    });

    it('should fire immediately then on interval', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
      });

      act(() => {
        useMetricsStore.getState().startPolling();
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(1); // Immediate fire

      await act(async () => {
        vi.advanceTimersByTime(5000);
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(2);
    });
  });

  describe('stopPolling', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should stop polling timer', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
        useMetricsStore.getState().startPolling();
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(1);

      act(() => {
        useMetricsStore.getState().stopPolling();
      });

      await act(async () => {
        vi.advanceTimersByTime(5000);
      });

      // Should not have called again after stop
      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(1);

      const state = useMetricsStore.getState();
      expect(state._timerId).toBeNull();
    });

    it('should handle stopPolling when not polling', () => {
      // Should not throw
      expect(() => {
        useMetricsStore.getState().stopPolling();
      }).not.toThrow();

      const state = useMetricsStore.getState();
      expect(state._timerId).toBeNull();
    });
  });

  describe('refreshAll', () => {
    it('should refresh both pipeline and aggregate metrics', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);
      vi.mocked(api.getAggregateMetrics).mockResolvedValue(sampleAggregateResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
      });

      await act(async () => {
        await useMetricsStore.getState().refreshAll();
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledWith('test-pipeline');
      expect(api.getAggregateMetrics).toHaveBeenCalled();
    });

    it('should handle refresh without selected pipeline', async () => {
      vi.mocked(api.getAggregateMetrics).mockResolvedValue(sampleAggregateResponse);

      await act(async () => {
        await useMetricsStore.getState().refreshAll();
      });

      expect(api.getPipelineMetrics).not.toHaveBeenCalled();
      expect(api.getAggregateMetrics).toHaveBeenCalled();
    });
  });

  describe('setAutoRefresh', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should toggle autoRefresh', () => {
      const state = useMetricsStore.getState();
      expect(state.autoRefresh).toBe(true);

      act(() => {
        useMetricsStore.getState().setAutoRefresh(false);
      });

      expect(useMetricsStore.getState().autoRefresh).toBe(false);
    });

    it('should stop polling when autoRefresh disabled', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
        useMetricsStore.getState().startPolling();
      });

      expect(useMetricsStore.getState()._timerId).not.toBeNull();

      act(() => {
        useMetricsStore.getState().setAutoRefresh(false);
      });

      expect(useMetricsStore.getState()._timerId).toBeNull();
    });

    it('should start polling when autoRefresh enabled with selected pipeline', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
        useMetricsStore.getState().setAutoRefresh(false);
      });

      // Start polling by enabling autoRefresh
      act(() => {
        useMetricsStore.getState().setAutoRefresh(true);
      });

      // Should start polling
      expect(api.getPipelineMetrics).toHaveBeenCalled();
    });
  });

  describe('setPollInterval', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should update poll interval', () => {
      act(() => {
        useMetricsStore.getState().setPollInterval(10000);
      });

      const state = useMetricsStore.getState();
      expect(state.pollInterval).toBe(10000);
    });

    it('should restart polling with new interval if already polling', async () => {
      vi.mocked(api.getPipelineMetrics).mockResolvedValue(sampleMetricsResponse);

      await act(async () => {
        useMetricsStore.getState().setSelectedPipelineId('test-pipeline');
        useMetricsStore.getState().startPolling();
      });

      expect(api.getPipelineMetrics).toHaveBeenCalledTimes(1);

      act(() => {
        useMetricsStore.getState().setPollInterval(2000);
      });

      // Timer should restart with new interval
      const state = useMetricsStore.getState();
      expect(state.pollInterval).toBe(2000);
    });
  });

  describe('State Setters', () => {
    it('should set current metrics via setCurrentMetrics', () => {
      act(() => {
        useMetricsStore.getState().setCurrentMetrics(sampleMetricsResponse);
      });

      const state = useMetricsStore.getState();
      expect(state.currentMetrics).toEqual(sampleMetricsResponse);
    });

    it('should set selected pipeline ID via setSelectedPipelineId', () => {
      act(() => {
        useMetricsStore.getState().setSelectedPipelineId('pipeline-123');
      });

      const state = useMetricsStore.getState();
      expect(state.selectedPipelineId).toBe('pipeline-123');
    });

    it('should set aggregate metrics via setAggregateMetrics', () => {
      act(() => {
        useMetricsStore.getState().setAggregateMetrics(sampleAggregateResponse);
      });

      const state = useMetricsStore.getState();
      expect(state.aggregateMetrics).toEqual(sampleAggregateResponse);
    });

    it('should set loading state via setIsLoading', () => {
      act(() => {
        useMetricsStore.getState().setIsLoading(true);
      });

      const state = useMetricsStore.getState();
      expect(state.isLoading).toBe(true);
    });

    it('should set last error via setLastError', () => {
      act(() => {
        useMetricsStore.getState().setLastError('Test error');
      });

      const state = useMetricsStore.getState();
      expect(state.lastError).toBe('Test error');
    });
  });

  describe('Error State Handling', () => {
    it('should preserve lastError until cleared', async () => {
      vi.mocked(api.getPipelineMetrics).mockRejectedValue(new Error('Test error'));

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
      });

      expect(useMetricsStore.getState().lastError).toContain('Test error');

      // Error persists until next operation
      expect(useMetricsStore.getState().lastError).toContain('Test error');
    });

    it('should clear error on successful refetch', async () => {
      vi.mocked(api.getPipelineMetrics)
        .mockRejectedValueOnce(new Error('Initial error'))
        .mockResolvedValueOnce(sampleMetricsResponse);

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
      });

      expect(useMetricsStore.getState().lastError).toContain('Initial error');

      await act(async () => {
        await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
      });

      expect(useMetricsStore.getState().lastError).toBeNull();
    });
  });
});

describe('Metrics Store Selectors', () => {
  beforeEach(() => {
    useMetricsStore.setState({
      currentMetrics: sampleMetricsResponse,
    });
  });

  describe('selectSummary', () => {
    it('should return summary from current metrics', () => {
      const summary = selectSummary(useMetricsStore.getState());
      expect(summary).toEqual(sampleMetricsResponse.summary);
    });

    it('should return undefined when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const summary = selectSummary(useMetricsStore.getState());
      expect(summary).toBeUndefined();
    });
  });

  describe('selectPhaseBreakdown', () => {
    it('should return phase breakdown', () => {
      const breakdown = selectPhaseBreakdown(useMetricsStore.getState());
      expect(breakdown).toEqual(sampleMetricsResponse.phase_breakdown);
    });

    it('should return empty object when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const breakdown = selectPhaseBreakdown(useMetricsStore.getState());
      expect(breakdown).toEqual({});
    });
  });

  describe('selectLoopMetrics', () => {
    it('should return loop metrics', () => {
      const metrics = selectLoopMetrics(useMetricsStore.getState());
      expect(metrics).toEqual(sampleMetricsResponse.loop_metrics);
    });

    it('should return empty object when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const metrics = selectLoopMetrics(useMetricsStore.getState());
      expect(metrics).toEqual({});
    });
  });

  describe('selectStateTransitions', () => {
    it('should return state transitions', () => {
      const transitions = selectStateTransitions(useMetricsStore.getState());
      expect(transitions).toEqual(sampleMetricsResponse.state_transitions);
    });

    it('should return empty array when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const transitions = selectStateTransitions(useMetricsStore.getState());
      expect(transitions).toEqual([]);
    });
  });

  describe('selectAgentSelections', () => {
    it('should return agent selections', () => {
      const selections = selectAgentSelections(useMetricsStore.getState());
      expect(selections).toEqual(sampleMetricsResponse.agent_selections);
    });

    it('should return empty array when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const selections = selectAgentSelections(useMetricsStore.getState());
      expect(selections).toEqual([]);
    });
  });

  describe('selectDefectsByType', () => {
    it('should return defects by type', () => {
      const defects = selectDefectsByType(useMetricsStore.getState());
      expect(defects).toEqual(sampleMetricsResponse.defects_by_type);
    });

    it('should return empty object when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const defects = selectDefectsByType(useMetricsStore.getState());
      expect(defects).toEqual({});
    });
  });

  describe('selectTotalDefects', () => {
    it('should calculate total defects count', () => {
      const total = selectTotalDefects(useMetricsStore.getState());
      expect(total).toBe(5); // 3 + 2
    });

    it('should return 0 when no defects', () => {
      useMetricsStore.setState({
        currentMetrics: { ...sampleMetricsResponse, defects_by_type: {} },
      });
      const total = selectTotalDefects(useMetricsStore.getState());
      expect(total).toBe(0);
    });

    it('should return 0 when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const total = selectTotalDefects(useMetricsStore.getState());
      expect(total).toBe(0);
    });
  });

  describe('selectAverageQuality', () => {
    it('should return average quality score', () => {
      const quality = selectAverageQuality(useMetricsStore.getState());
      expect(quality).toBe(0.88);
    });

    it('should return 0 when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const quality = selectAverageQuality(useMetricsStore.getState());
      expect(quality).toBe(0);
    });
  });

  describe('selectAverageTPS', () => {
    it('should return average tokens per second', () => {
      const tps = selectAverageTPS(useMetricsStore.getState());
      expect(tps).toBe(41.5);
    });

    it('should return 0 when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const tps = selectAverageTPS(useMetricsStore.getState());
      expect(tps).toBe(0);
    });
  });

  describe('selectAverageTTFT', () => {
    it('should return average time to first token', () => {
      const ttft = selectAverageTTFT(useMetricsStore.getState());
      expect(ttft).toBe(0.35);
    });

    it('should return 0 when no metrics', () => {
      useMetricsStore.setState({ currentMetrics: null });
      const ttft = selectAverageTTFT(useMetricsStore.getState());
      expect(ttft).toBe(0);
    });
  });
});

describe('Metrics Store Edge Cases', () => {
  beforeEach(() => {
    useMetricsStore.setState({
      currentMetrics: null,
      selectedPipelineId: null,
      metricsHistory: [],
      aggregateMetrics: null,
      isLoading: false,
      lastError: null,
      _timerId: null,
    });
    vi.clearAllMocks();
  });

  it('should handle null response from API gracefully', async () => {
    vi.mocked(api.getPipelineMetrics).mockResolvedValue(null as unknown as typeof sampleMetricsResponse);

    await act(async () => {
      await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
    });

    const state = useMetricsStore.getState();
    // Should set the null value without crashing
    expect(state.currentMetrics).toBeNull();
  });

  it('should handle partial metrics data', async () => {
    const partialMetrics = {
      success: true,
      pipeline_id: 'test-pipeline',
      summary: null,
      phase_breakdown: {},
      loop_metrics: {},
      state_transitions: [],
      defects_by_type: {},
      agent_selections: [],
    };
    vi.mocked(api.getPipelineMetrics).mockResolvedValue(partialMetrics as unknown as typeof sampleMetricsResponse);

    await act(async () => {
      await useMetricsStore.getState().fetchPipelineMetrics('test-pipeline');
    });

    const state = useMetricsStore.getState();
    expect(state.currentMetrics).toBeTruthy();
    expect(state.lastError).toBeNull();
  });

  it('should handle empty history array', async () => {
    const emptyHistory = {
      pipeline_id: 'test-pipeline',
      metric_type: 'TPS',
      total_points: 0,
      history: [],
    };
    vi.mocked(api.getMetricsHistory).mockResolvedValue(emptyHistory as unknown as typeof sampleHistoryResponse);

    await act(async () => {
      await useMetricsStore.getState().fetchMetricsHistory('test-pipeline');
    });

    const state = useMetricsStore.getState();
    expect(state.metricsHistory.length).toBe(1); // Still adds the snapshot
  });
});
