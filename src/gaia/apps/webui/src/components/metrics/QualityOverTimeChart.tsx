// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * QualityOverTimeChart - Line chart showing quality score trends.
 */

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import './QualityOverTimeChart.css';

interface QualityOverTimeChartProps {
  qualityHistory: {
    loop_id: string;
    phase: string;
    score: number;
    timestamp: string;
  }[];
}

export function QualityOverTimeChart({ qualityHistory }: QualityOverTimeChartProps) {
  // Transform data for chart
  const data = (qualityHistory || []).map((item, index) => ({
    index: index + 1,
    score: item.score,
    scorePercent: Math.round(item.score * 100),
    loop_id: item.loop_id,
    phase: item.phase,
    timestamp: item.timestamp,
  }));

  // Calculate trend
  const getTrend = () => {
    if (data.length < 2) return 'stable';
    const firstHalf = data.slice(0, Math.floor(data.length / 2));
    const secondHalf = data.slice(Math.floor(data.length / 2));
    const firstAvg = firstHalf.reduce((sum, d) => sum + d.score, 0) / firstHalf.length;
    const secondAvg = secondHalf.reduce((sum, d) => sum + d.score, 0) / secondHalf.length;
    const diff = secondAvg - firstAvg;
    if (diff > 0.05) return 'up';
    if (diff < -0.05) return 'down';
    return 'stable';
  };

  const trend = getTrend();
  const avgScore = data.length > 0 ? data.reduce((sum, d) => sum + d.score, 0) / data.length : 0;
  const maxScore = data.length > 0 ? Math.max(...data.map((d) => d.score)) : 0;
  const minScore = data.length > 0 ? Math.min(...data.map((d) => d.score)) : 0;

  const formatYAxis = (value: number): string => `${Math.round(value * 100)}%`;

  const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: unknown[] }) => {
    if (active && payload && payload.length) {
      const point = payload[0].payload;
      return (
        <div className="quality-tooltip">
          <p className="quality-tooltip-phase">{point.phase}</p>
          <p className="quality-tooltip-loop">Loop: {point.loop_id}</p>
          <p className="quality-tooltip-score">
            Quality: <strong>{(point.score * 100).toFixed(0)}%</strong>
          </p>
        </div>
      );
    }
    return null;
  };

  if (!data || data.length === 0) {
    return (
      <div className="quality-chart quality-chart-empty">
        <div className="chart-empty-state">
          <span className="empty-icon">📈</span>
          <p>No quality history data available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="quality-chart">
      <div className="quality-header">
        <div className="quality-title">
          <h3>Quality Over Time</h3>
          <div className={`quality-trend ${trend}`}>
            {trend === 'up' && <TrendingUp size={14} />}
            {trend === 'down' && <TrendingDown size={14} />}
            {trend === 'stable' && <Minus size={14} />}
            <span>{trend === 'up' ? 'Improving' : trend === 'down' ? 'Declining' : 'Stable'}</span>
          </div>
        </div>
        <div className="quality-stats">
          <div className="quality-stat">
            <span className="quality-stat-label">Avg</span>
            <span className="quality-stat-value">{(avgScore * 100).toFixed(0)}%</span>
          </div>
          <div className="quality-stat">
            <span className="quality-stat-label">Max</span>
            <span className="quality-stat-value quality-max">{(maxScore * 100).toFixed(0)}%</span>
          </div>
          <div className="quality-stat">
            <span className="quality-stat-label">Min</span>
            <span className="quality-stat-value quality-min">{(minScore * 100).toFixed(0)}%</span>
          </div>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={250}>
        <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="qualityGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="index"
            tick={{ fontSize: 10, fill: '#64748b' }}
            tickLine={false}
            axisLine={{ stroke: '#e2e8f0' }}
            tickFormatter={(v) => v.toString()}
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fontSize: 10, fill: '#64748b' }}
            tickLine={false}
            axisLine={{ stroke: '#e2e8f0' }}
            tickFormatter={formatYAxis}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#3b82f6', strokeWidth: 1 }} />
          <Area
            type="monotone"
            dataKey="score"
            stroke="#3b82f6"
            strokeWidth={2}
            fill="url(#qualityGradient)"
            dot={{ fill: '#3b82f6', r: 3 }}
            activeDot={{ r: 5, strokeWidth: 0 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
