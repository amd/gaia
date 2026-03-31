// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * MetricsDashboard - Main dashboard for pipeline metrics.
 */

import { useEffect, useState, useCallback } from 'react';
import { RefreshCw, Settings, Play, Pause, BarChart3, AlertCircle } from 'lucide-react';
import { useMetricsStore } from '../../stores/metricsStore';
import { MetricSummaryCards } from './MetricSummaryCards';
import { PhaseTimingChart } from './PhaseTimingChart';
import { QualityOverTimeChart } from './QualityOverTimeChart';
import './MetricsDashboard.css';

interface MetricsDashboardProps {
  pipelineId?: string; // Optional - if not provided, shows aggregate metrics
}

export function MetricsDashboard({ pipelineId }: MetricsDashboardProps) {
  const {
    currentMetrics,
    aggregateMetrics,
    autoRefresh,
    isLoading,
    lastError,
    selectedPipelineId,
    setSelectedPipelineId,
    fetchPipelineMetrics,
    fetchAggregateMetrics,
    setAutoRefresh,
    startPolling,
    stopPolling,
  } = useMetricsStore();

  const [showCharts, setShowCharts] = useState(true);

  // Initialize on mount
  useEffect(() => {
    const id = pipelineId || selectedPipelineId;
    if (id) {
      setSelectedPipelineId(id);
      fetchPipelineMetrics(id);
    }
    fetchAggregateMetrics();

    // Start polling if auto-refresh is enabled
    if (autoRefresh && id) {
      startPolling();
    }

    return () => {
      stopPolling();
    };
  }, []);

  // Update pipeline ID when prop changes
  useEffect(() => {
    if (pipelineId && pipelineId !== selectedPipelineId) {
      setSelectedPipelineId(pipelineId);
      fetchPipelineMetrics(pipelineId);
    }
  }, [pipelineId]);

  // Handle auto-refresh toggle
  useEffect(() => {
    if (autoRefresh && selectedPipelineId) {
      startPolling();
    } else {
      stopPolling();
    }
  }, [autoRefresh, selectedPipelineId]);

  const handleRefresh = useCallback(() => {
    if (selectedPipelineId) {
      fetchPipelineMetrics(selectedPipelineId);
    }
    fetchAggregateMetrics();
  }, [selectedPipelineId]);

  const handleToggleAutoRefresh = () => {
    setAutoRefresh(!autoRefresh);
  };

  const metrics = pipelineId ? currentMetrics : aggregateMetrics;
  const summary = currentMetrics?.summary || null;

  return (
    <div className="metrics-dashboard">
      <div className="metrics-header">
        <div className="metrics-title">
          <BarChart3 size={24} className="metrics-title-icon" />
          <div>
            <h1>Pipeline Metrics</h1>
            <p>
              {pipelineId
                ? `Metrics for pipeline: ${pipelineId}`
                : 'Aggregate metrics across all pipelines'}
            </p>
          </div>
        </div>
        <div className="metrics-actions">
          <button
            className={`metrics-btn ${autoRefresh ? 'active' : ''}`}
            onClick={handleToggleAutoRefresh}
            title={autoRefresh ? 'Pause auto-refresh' : 'Enable auto-refresh'}
            aria-label="Toggle auto-refresh"
          >
            {autoRefresh ? <Pause size={16} /> : <Play size={16} />}
            <span className="btn-label">{autoRefresh ? 'Live' : 'Paused'}</span>
          </button>
          <button
            className="metrics-btn metrics-btn-refresh"
            onClick={handleRefresh}
            disabled={isLoading}
            title="Refresh metrics"
            aria-label="Refresh metrics"
          >
            <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
          </button>
          <button
            className="metrics-btn"
            onClick={() => setShowCharts(!showCharts)}
            title="Toggle charts"
            aria-label="Toggle charts"
          >
            <Settings size={16} />
          </button>
        </div>
      </div>

      {lastError && (
        <div className="metrics-error-banner" role="alert">
          <AlertCircle size={18} />
          <span>{lastError}</span>
        </div>
      )}

      {/* Summary Cards */}
      <MetricSummaryCards summary={summary} />

      {/* Charts */}
      {showCharts && currentMetrics && (
        <div className="metrics-charts">
          <div className="metrics-chart-row">
            <div className="metrics-chart-full">
              <PhaseTimingChart phaseBreakdown={currentMetrics.phase_breakdown} />
            </div>
          </div>
          <div className="metrics-chart-row">
            <div className="metrics-chart-full">
              <QualityOverTimeChart
                qualityHistory={(currentMetrics.quality_scores || []).map(([loop_id, phase, score]) => ({
                  loop_id,
                  phase,
                  score,
                  timestamp: new Date().toISOString(),
                }))}
              />
            </div>
          </div>
        </div>
      )}

      {/* Additional Details */}
      {currentMetrics && (
        <div className="metrics-details">
          <div className="metrics-section">
            <h3>State Transitions</h3>
            {currentMetrics.state_transitions.length > 0 ? (
              <div className="state-transitions-list">
                {currentMetrics.state_transitions.map((transition, index) => (
                  <div key={index} className="state-transition-item">
                    <span className="state-from">{transition.from_state}</span>
                    <span className="state-arrow">→</span>
                    <span className="state-to">{transition.to_state}</span>
                    <span className="state-reason">{transition.reason}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="metrics-empty">No state transitions recorded</p>
            )}
          </div>

          <div className="metrics-section">
            <h3>Agent Selections</h3>
            {currentMetrics.agent_selections.length > 0 ? (
              <div className="agent-selections-list">
                {currentMetrics.agent_selections.map((selection, index) => (
                  <div key={index} className="agent-selection-item">
                    <div className="agent-selection-header">
                      <span className="agent-phase">{selection.phase}</span>
                      <span className="agent-id">{selection.agent_id}</span>
                    </div>
                    <p className="agent-reason">{selection.reason}</p>
                    {selection.alternatives.length > 0 && (
                      <p className="agent-alternatives">
                        Alternatives: {selection.alternatives.join(', ')}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="metrics-empty">No agent selections recorded</p>
            )}
          </div>

          <div className="metrics-section">
            <h3>Defects by Type</h3>
            {currentMetrics.defects_by_type &&
            Object.keys(currentMetrics.defects_by_type).length > 0 ? (
              <div className="defects-list">
                {Object.entries(currentMetrics.defects_by_type).map(([type, count]) => (
                  <div key={type} className="defect-item">
                    <span className="defect-type">{type}</span>
                    <span className="defect-count">{count}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="metrics-empty">No defects recorded</p>
            )}
          </div>
        </div>
      )}

      {isLoading && !currentMetrics && (
        <div className="metrics-loading">
          <div className="loading-spinner" />
          <span>Loading metrics...</span>
        </div>
      )}
    </div>
  );
}
