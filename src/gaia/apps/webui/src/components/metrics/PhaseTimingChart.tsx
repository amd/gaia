// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PhaseTimingChart - Bar chart showing phase timing breakdown.
 */

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import './PhaseTimingChart.css';

interface PhaseTimingChartProps {
  phaseBreakdown: Record<string, {
    phase_name: string;
    duration_seconds: number;
    token_count: number;
    tps: number;
    ttft?: number;
  }>;
}

const COLORS = ['#3b82f6', '#22c55e', '#eab308', '#ef4444', '#a855f7', '#ec4899', '#06b6d4', '#f97316'];

export function PhaseTimingChart({ phaseBreakdown }: PhaseTimingChartProps) {
  const data = Object.entries(phaseBreakdown || {}).map(([key, phase], index) => ({
    name: phase.phase_name || key,
    duration: phase.duration_seconds,
    tokens: phase.token_count,
    tps: phase.tps,
    ttft: phase.ttft,
    color: COLORS[index % COLORS.length],
  }));

  const formatDuration = (seconds: number): string => {
    if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
    if (seconds < 60) return `${seconds.toFixed(2)}s`;
    return `${(seconds / 60).toFixed(1)}m`;
  };

  const formatTooltipValue = (value: number, name: string) => {
    if (name === 'duration') return formatDuration(value);
    if (name === 'tps') return `${value.toFixed(1)} tok/s`;
    if (name === 'ttft' && value) return `${value.toFixed(3)}s`;
    return value.toString();
  };

  const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: unknown[] }) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="phase-timing-tooltip">
          <p className="phase-timing-tooltip-name">{data.name}</p>
          <p className="phase-timing-tooltip-item">
            <span className="tooltip-label">Duration:</span> {formatDuration(data.duration)}
          </p>
          <p className="phase-timing-tooltip-item">
            <span className="tooltip-label">Tokens:</span> {data.tokens}
          </p>
          <p className="phase-timing-tooltip-item">
            <span className="tooltip-label">TPS:</span> {data.tps.toFixed(1)}
          </p>
          {data.ttft && (
            <p className="phase-timing-tooltip-item">
              <span className="tooltip-label">TTFT:</span> {formatDuration(data.ttft)}
            </p>
          )}
        </div>
      );
    }
    return null;
  };

  if (!data || data.length === 0) {
    return (
      <div className="phase-timing-chart phase-timing-chart-empty">
        <div className="chart-empty-state">
          <span className="empty-icon">📊</span>
          <p>No phase timing data available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="phase-timing-chart">
      <div className="phase-timing-header">
        <h3>Phase Timing Breakdown</h3>
        <span className="phase-timing-subtitle">
          {data.length} phase{data.length !== 1 ? 's' : ''}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11, fill: '#64748b' }}
            tickLine={false}
            axisLine={{ stroke: '#e2e8f0' }}
            interval={0}
            angle={-15}
            textAnchor="end"
            height={60}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#64748b' }}
            tickLine={false}
            axisLine={{ stroke: '#e2e8f0' }}
            tickFormatter={formatDuration}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.05)' }} />
          <Bar dataKey="duration" radius={[4, 4, 0, 0]}>
            {data.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
