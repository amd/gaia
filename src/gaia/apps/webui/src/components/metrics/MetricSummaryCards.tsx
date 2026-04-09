// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * MetricSummaryCards - Summary metric cards for the dashboard.
 */

import { Clock, Zap, Target, AlertTriangle, CheckCircle, TrendingUp, Activity, Layers } from 'lucide-react';
import './MetricSummaryCards.css';

interface MetricSummaryCardsProps {
  summary: {
    total_duration_seconds?: number;
    total_tokens?: number;
    avg_tps?: number;
    avg_ttft?: number;
    total_loops?: number;
    total_iterations?: number;
    total_defects?: number;
    avg_quality_score?: number;
    max_quality_score?: number;
  } | null;
}

interface MetricCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  subtext?: string;
  trend?: 'up' | 'down' | 'stable';
  color?: 'blue' | 'green' | 'yellow' | 'red' | 'purple';
}

function MetricCard({ icon, label, value, subtext, trend, color = 'blue' }: MetricCardProps) {
  return (
    <div className={`metric-card metric-card-${color}`}>
      <div className="metric-card-icon">{icon}</div>
      <div className="metric-card-content">
        <span className="metric-card-label">{label}</span>
        <div className="metric-card-value">
          {value}
          {trend && (
            <TrendingUp
              size={14}
              className={`metric-trend ${trend === 'up' ? 'trend-up' : trend === 'down' ? 'trend-down' : ''}`}
            />
          )}
        </div>
        {subtext && <span className="metric-card-subtext">{subtext}</span>}
      </div>
    </div>
  );
}

export function MetricSummaryCards({ summary }: MetricSummaryCardsProps) {
  if (!summary) {
    return (
      <div className="metric-summary-cards">
        <div className="metric-card metric-card-blue">
          <div className="metric-card-icon"><Activity size={18} /></div>
          <div className="metric-card-content">
            <span className="metric-card-label">No Data</span>
          </div>
        </div>
      </div>
    );
  }

  const formatDuration = (seconds: number): string => {
    if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  const formatQuality = (score: number): string => `${(score * 100).toFixed(0)}%`;

  const qualityColor = (score: number): string => {
    if (score >= 0.9) return 'green';
    if (score >= 0.7) return 'yellow';
    return 'red';
  };

  return (
    <div className="metric-summary-cards">
      <MetricCard
        icon={<Clock size={18} />}
        label="Total Duration"
        value={formatDuration(summary.total_duration_seconds || 0)}
        color="blue"
      />
      <MetricCard
        icon={<Zap size={18} />}
        label="Avg Tokens/sec"
        value={summary.avg_tps?.toFixed(1) || '0'}
        subtext={`${summary.total_tokens || 0} total tokens`}
        trend={summary.avg_tps && summary.avg_tps > 50 ? 'up' : 'stable'}
        color="green"
      />
      <MetricCard
        icon={<Target size={18} />}
        label="Avg TTFT"
        value={`${(summary.avg_ttft || 0).toFixed(3)}s`}
        color="purple"
      />
      <MetricCard
        icon={<Layers size={18} />}
        label="Loops"
        value={`${summary.total_loops || 0}`}
        subtext={`${summary.total_iterations || 0} iterations`}
        color="blue"
      />
      <MetricCard
        icon={<CheckCircle size={18} />}
        label="Avg Quality"
        value={formatQuality(summary.avg_quality_score || 0)}
        subtext={`Max: ${formatQuality(summary.max_quality_score || 0)}`}
        trend={summary.avg_quality_score && summary.avg_quality_score >= 0.9 ? 'up' : 'stable'}
        color={qualityColor(summary.avg_quality_score || 0)}
      />
      <MetricCard
        icon={<AlertTriangle size={18} />}
        label="Total Defects"
        value={`${summary.total_defects || 0}`}
        color={summary.total_defects === 0 ? 'green' : summary.total_defects < 5 ? 'yellow' : 'red'}
      />
    </div>
  );
}
