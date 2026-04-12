// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Unit tests for MetricsDashboard component.
 *
 * Tests cover:
 * - Dashboard rendering with metrics data
 * - Auto-refresh toggle functionality
 * - Loading and error states
 * - Chart visibility toggle
 * - Metrics display sections
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MetricsDashboard } from '../metrics/MetricsDashboard';

// Mock the metrics store
const mockFetchPipelineMetrics = vi.fn();
const mockFetchAggregateMetrics = vi.fn();
const mockSetSelectedPipelineId = vi.fn();
const mockSetAutoRefresh = vi.fn();
const mockStartPolling = vi.fn();
const mockStopPolling = vi.fn();

vi.mock('../../stores/metricsStore', () => ({
  useMetricsStore: vi.fn(() => ({
    currentMetrics: null,
    aggregateMetrics: null,
    autoRefresh: true,
    isLoading: false,
    lastError: null,
    selectedPipelineId: null,
    setSelectedPipelineId: mockSetSelectedPipelineId,
    fetchPipelineMetrics: mockFetchPipelineMetrics,
    fetchAggregateMetrics: mockFetchAggregateMetrics,
    setAutoRefresh: mockSetAutoRefresh,
    startPolling: mockStartPolling,
    stopPolling: mockStopPolling,
  })),
  selectSummary: vi.fn((state) => state.currentMetrics?.summary),
  selectPhaseBreakdown: vi.fn((state) => state.currentMetrics?.phase_breakdown || {}),
  selectLoopMetrics: vi.fn((state) => state.currentMetrics?.loop_metrics || {}),
  selectStateTransitions: vi.fn((state) => state.currentMetrics?.state_transitions || []),
  selectAgentSelections: vi.fn((state) => state.currentMetrics?.agent_selections || []),
  selectDefectsByType: vi.fn((state) => state.currentMetrics?.defects_by_type || {}),
  selectTotalDefects: vi.fn((state) => {
    const defects = state.currentMetrics?.defects_by_type || {};
    return Object.values(defects).reduce((sum, count) => sum + count, 0);
  }),
  selectAverageQuality: vi.fn((state) => state.currentMetrics?.summary?.avg_quality_score || 0),
  selectAverageTPS: vi.fn((state) => state.currentMetrics?.summary?.avg_tps || 0),
  selectAverageTTFT: vi.fn((state) => state.currentMetrics?.summary?.avg_ttft || 0),
}));

// Mock child components
vi.mock('../metrics/MetricSummaryCards', () => ({
  MetricSummaryCards: ({ summary }: { summary: unknown }) => (
    <div data-testid="metric-summary-cards">
      {summary ? 'Summary Data' : 'No Summary'}
    </div>
  ),
}));

vi.mock('../metrics/PhaseTimingChart', () => ({
  PhaseTimingChart: ({ phaseBreakdown }: { phaseBreakdown: unknown }) => (
    <div data-testid="phase-timing-chart">
      Phase Timing: {JSON.stringify(phaseBreakdown)}
    </div>
  ),
}));

vi.mock('../metrics/QualityOverTimeChart', () => ({
  QualityOverTimeChart: ({ qualityHistory }: { qualityHistory: unknown }) => (
    <div data-testid="quality-over-time-chart">
      Quality Over Time: {JSON.stringify(qualityHistory)}
    </div>
  ),
}));

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  RefreshCw: ({ size }: { size?: number }) => <svg data-testid="refresh-icon" width={size} />,
  Settings: ({ size }: { size?: number }) => <svg data-testid="settings-icon" width={size} />,
  Play: ({ size }: { size?: number }) => <svg data-testid="play-icon" width={size} />,
  Pause: ({ size }: { size?: number }) => <svg data-testid="pause-icon" />,
  BarChart3: ({ size }: { size?: number }) => <svg data-testid="barchart-icon" width={size} />,
  AlertCircle: ({ size }: { size?: number }) => <svg data-testid="alert-icon" width={size} />,
}));

const sampleMetrics = {
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
      duration_seconds: 25.3,
      tps: 35.2,
      ttft: 0.4,
    },
    DEVELOPMENT: {
      phase_name: 'DEVELOPMENT',
      duration_seconds: 65.0,
      tps: 45.0,
      ttft: 0.3,
    },
  },
  loop_metrics: {
    'loop-001': {
      loop_id: 'loop-001',
      phase_name: 'DEVELOPMENT',
      iteration_count: 3,
      quality_scores: [0.65, 0.78, 0.92],
      average_quality: 0.78,
      defects_by_type: { testing: 2 },
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
  ],
  defects_by_type: {
    testing: 3,
    documentation: 2,
  },
  agent_selections: [
    {
      phase: 'PLANNING',
      agent_id: 'senior-developer',
      reason: 'Best match for requirements',
      alternatives: ['architect'],
      timestamp: '2025-01-01T10:00:00Z',
    },
  ],
  quality_scores: [['loop-001', 'DEVELOPMENT', 0.88]],
};

const useMetricsStoreMock = vi.mocked(await import('../../stores/metricsStore')).useMetricsStore;

describe('MetricsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Reset mock store to default state
    useMetricsStoreMock.mockReturnValue({
      currentMetrics: null,
      aggregateMetrics: null,
      autoRefresh: true,
      isLoading: false,
      lastError: null,
      selectedPipelineId: null,
      setSelectedPipelineId: mockSetSelectedPipelineId,
      fetchPipelineMetrics: mockFetchPipelineMetrics,
      fetchAggregateMetrics: mockFetchAggregateMetrics,
      setAutoRefresh: mockSetAutoRefresh,
      startPolling: mockStartPolling,
      stopPolling: mockStopPolling,
    });
  });

  describe('Initial Rendering', () => {
    it('should render dashboard header', () => {
      render(<MetricsDashboard />);

      expect(screen.getByText('Pipeline Metrics')).toBeInTheDocument();
      expect(screen.getByText('Aggregate metrics across all pipelines')).toBeInTheDocument();
    });

    it('should render with pipeline ID prop', () => {
      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText(/Metrics for pipeline: pipeline-123/i)).toBeInTheDocument();
      expect(mockSetSelectedPipelineId).toHaveBeenCalledWith('pipeline-123');
      expect(mockFetchPipelineMetrics).toHaveBeenCalledWith('pipeline-123');
    });

    it('should render metric summary cards', () => {
      render(<MetricsDashboard />);

      expect(screen.getByTestId('metric-summary-cards')).toBeInTheDocument();
    });

    it('should render action buttons', () => {
      render(<MetricsDashboard />);

      // Auto-refresh toggle button
      expect(screen.getByRole('button', { name: /pause auto-refresh/i })).toBeInTheDocument();

      // Refresh button
      expect(screen.getByRole('button', { name: /refresh metrics/i })).toBeInTheDocument();

      // Settings toggle button
      expect(screen.getByRole('button', { name: /toggle charts/i })).toBeInTheDocument();
    });

    it('should show Live status when auto-refresh is enabled', () => {
      render(<MetricsDashboard />);

      expect(screen.getByText('Live')).toBeInTheDocument();
    });
  });

  describe('Auto-Refresh Toggle', () => {
    it('should call setAutoRefresh when toggle button is clicked', () => {
      render(<MetricsDashboard />);

      const toggleButton = screen.getByRole('button', { name: /pause auto-refresh/i });
      fireEvent.click(toggleButton);

      expect(mockSetAutoRefresh).toHaveBeenCalledWith(false);
    });

    it('should show Paused status when auto-refresh is disabled', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: false,
        isLoading: false,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByText('Paused')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /enable auto-refresh/i })).toBeInTheDocument();
    });

    it('should show Play icon when auto-refresh is disabled', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: false,
        isLoading: false,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByTestId('play-icon')).toBeInTheDocument();
    });

    it('should show Pause icon when auto-refresh is enabled', () => {
      render(<MetricsDashboard />);

      expect(screen.getByTestId('pause-icon')).toBeInTheDocument();
    });
  });

  describe('Refresh Button', () => {
    it('should call fetchPipelineMetrics and fetchAggregateMetrics when refresh is clicked', async () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      const refreshButton = screen.getByRole('button', { name: /refresh metrics/i });
      fireEvent.click(refreshButton);

      await waitFor(() => {
        expect(mockFetchPipelineMetrics).toHaveBeenCalledWith('pipeline-123');
        expect(mockFetchAggregateMetrics).toHaveBeenCalled();
      });
    });

    it('should be disabled during loading', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: true,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      const refreshButton = screen.getByRole('button', { name: /refresh metrics/i });
      expect(refreshButton).toBeDisabled();
    });
  });

  describe('Charts Toggle', () => {
    it('should show charts by default', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByTestId('phase-timing-chart')).toBeInTheDocument();
      expect(screen.getByTestId('quality-over-time-chart')).toBeInTheDocument();
    });

    it('should hide charts when toggle is clicked', async () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      // Initially charts are visible
      expect(screen.getByTestId('phase-timing-chart')).toBeInTheDocument();

      // Click settings toggle
      const settingsButton = screen.getByRole('button', { name: /toggle charts/i });
      fireEvent.click(settingsButton);

      // Charts should be hidden after click
      await waitFor(() => {
        expect(screen.queryByTestId('phase-timing-chart')).not.toBeInTheDocument();
      });
    });
  });

  describe('Error State', () => {
    it('should display error banner when lastError is set', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: 'Failed to fetch metrics: Network error',
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByText('Failed to fetch metrics: Network error')).toBeInTheDocument();
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });

    it('should show alert icon in error banner', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: 'Error message',
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByTestId('alert-icon')).toBeInTheDocument();
    });

    it('should not show error banner when no error', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
  });

  describe('Loading State', () => {
    it('should show loading indicator when isLoading and no metrics', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: true,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByText('Loading metrics...')).toBeInTheDocument();
    });

    it('should not show loading when metrics are available', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: true,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.queryByText('Loading metrics...')).not.toBeInTheDocument();
    });
  });

  describe('Metrics Details Sections', () => {
    it('should display state transitions section', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('State Transitions')).toBeInTheDocument();
      expect(screen.getByText('INIT')).toBeInTheDocument();
      expect(screen.getByText('PLANNING')).toBeInTheDocument();
      expect(screen.getByText('Phase transition')).toBeInTheDocument();
    });

    it('should display agent selections section', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('Agent Selections')).toBeInTheDocument();
      expect(screen.getByText('PLANNING')).toBeInTheDocument();
      expect(screen.getByText('senior-developer')).toBeInTheDocument();
      expect(screen.getByText('Best match for requirements')).toBeInTheDocument();
    });

    it('should display defects by type section', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('Defects by Type')).toBeInTheDocument();
      expect(screen.getByText('testing')).toBeInTheDocument();
      expect(screen.getByText('3')).toBeInTheDocument();
      expect(screen.getByText('documentation')).toBeInTheDocument();
      expect(screen.getByText('2')).toBeInTheDocument();
    });

    it('should show empty state when no state transitions', () => {
      const metricsWithoutTransitions = {
        ...sampleMetrics,
        state_transitions: [],
      };

      useMetricsStoreMock.mockReturnValue({
        currentMetrics: metricsWithoutTransitions,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('No state transitions recorded')).toBeInTheDocument();
    });

    it('should show empty state when no agent selections', () => {
      const metricsWithoutAgents = {
        ...sampleMetrics,
        agent_selections: [],
      };

      useMetricsStoreMock.mockReturnValue({
        currentMetrics: metricsWithoutAgents,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('No agent selections recorded')).toBeInTheDocument();
    });

    it('should show empty state when no defects', () => {
      const metricsWithoutDefects = {
        ...sampleMetrics,
        defects_by_type: {},
      };

      useMetricsStoreMock.mockReturnValue({
        currentMetrics: metricsWithoutDefects,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText('No defects recorded')).toBeInTheDocument();
    });
  });

  describe('Alternative Agent Display', () => {
    it('should show alternatives when available', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: sampleMetrics,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.getByText(/Alternatives:/i)).toBeInTheDocument();
      expect(screen.getByText('architect')).toBeInTheDocument();
    });

    it('should not show alternatives section when empty', () => {
      const metricsWithNoAlternatives = {
        ...sampleMetrics,
        agent_selections: [
          {
            phase: 'PLANNING',
            agent_id: 'senior-developer',
            reason: 'Only option',
            alternatives: [],
            timestamp: '2025-01-01T10:00:00Z',
          },
        ],
      };

      useMetricsStoreMock.mockReturnValue({
        currentMetrics: metricsWithNoAlternatives,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: null,
        selectedPipelineId: 'pipeline-123',
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-123" />);

      expect(screen.queryByText(/Alternatives:/i)).not.toBeInTheDocument();
    });
  });

  describe('Effect Hooks', () => {
    it('should fetch metrics on mount with pipelineId prop', () => {
      render(<MetricsDashboard pipelineId="pipeline-456" />);

      expect(mockSetSelectedPipelineId).toHaveBeenCalledWith('pipeline-456');
      expect(mockFetchPipelineMetrics).toHaveBeenCalledWith('pipeline-456');
      expect(mockFetchAggregateMetrics).toHaveBeenCalled();
    });

    it('should start polling on mount if autoRefresh and pipelineId', () => {
      render(<MetricsDashboard pipelineId="pipeline-789" />);

      expect(mockStartPolling).toHaveBeenCalled();
    });

    it('should not start polling if autoRefresh is false', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: false,
        isLoading: false,
        lastError: null,
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard pipelineId="pipeline-789" />);

      expect(mockStartPolling).not.toHaveBeenCalled();
    });
  });

  describe('Accessibility', () => {
    it('should have proper heading structure', () => {
      render(<MetricsDashboard />);

      expect(screen.getByRole('heading', { name: 'Pipeline Metrics' })).toBeInTheDocument();
    });

    it('should have accessible button labels', () => {
      render(<MetricsDashboard />);

      expect(screen.getByRole('button', { name: /pause auto-refresh/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /refresh metrics/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /toggle charts/i })).toBeInTheDocument();
    });

    it('should have error banner with alert role', () => {
      useMetricsStoreMock.mockReturnValue({
        currentMetrics: null,
        aggregateMetrics: null,
        autoRefresh: true,
        isLoading: false,
        lastError: 'Error',
        selectedPipelineId: null,
        setSelectedPipelineId: mockSetSelectedPipelineId,
        fetchPipelineMetrics: mockFetchPipelineMetrics,
        fetchAggregateMetrics: mockFetchAggregateMetrics,
        setAutoRefresh: mockSetAutoRefresh,
        startPolling: mockStartPolling,
        stopPolling: mockStopPolling,
      });

      render(<MetricsDashboard />);

      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
