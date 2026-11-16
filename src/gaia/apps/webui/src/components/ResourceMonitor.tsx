// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { X } from 'lucide-react';
import { useResourceUsage } from '../hooks/useResourceUsage';
import { useT } from '../i18n/useT';
import { buildTiles, GRAPH_SLOTS, formatGb, formatRate, rateScale, rateUnit } from '../utils/resourceTiles.core';
import './ResourceMonitor.css';

export { buildTiles, GRAPH_SLOTS, formatGb, formatRate, rateScale, rateUnit };

/** Plot inset so a flat 0% line still shows above the bottom border. */
const INSET = 2;

function plot(values: number[], max: number): Array<[number, number]> {
    const pts = values.slice(-GRAPH_SLOTS);
    const offset = GRAPH_SLOTS - pts.length;
    return pts.map((v, i) => [
        ((offset + i) / (GRAPH_SLOTS - 1)) * 100,
        100 - INSET - (Math.max(0, Math.min(max, v)) / max) * (100 - 2 * INSET),
    ]);
}

function linePath(xy: Array<[number, number]>): string {
    return xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
}

interface SparklineProps {
    values: number[];
    /** The graph's top; 100 for a percentage. */
    max?: number;
    /** A second series drawn dashed, as Task Manager draws network send. */
    dashed?: number[];
}

/**
 * Task Manager's thumbnail graph of the last minute: a thin line over a light
 * fill and a faint grid, newest on the right.
 */
export function Sparkline({ values, max = 100, dashed }: SparklineProps) {
    const xy = plot(values, max);
    const line = linePath(xy);
    const area = xy.length > 1 ? `${line} L100 100 L${xy[0][0].toFixed(2)} 100 Z` : '';
    const second = dashed ? plot(dashed, max) : [];
    return (
        <svg className="resource-spark" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <path className="resource-spark-grid" d="M0 25H100M0 50H100M0 75H100M25 0V100M50 0V100M75 0V100" vectorEffect="non-scaling-stroke" />
            {area && <path className="resource-spark-area" d={area} />}
            {xy.length > 1 && <path className="resource-spark-line" d={line} vectorEffect="non-scaling-stroke" />}
            {second.length > 1 && <path className="resource-spark-line is-dashed" d={linePath(second)} vectorEffect="non-scaling-stroke" />}
        </svg>
    );
}


interface ResourceMonitorProps {
    onClose: () => void;
}

/** This PC's CPU, memory, GPU, NPU and network use as a strip of live graphs. */
export function ResourceMonitor({ onClose }: ResourceMonitorProps) {
    const t = useT();
    const { usage, error } = useResourceUsage(true);
    const tiles = usage?.ready ? buildTiles(t, usage) : [];

    return (
        <section className="resource-strip" aria-label={t('resources.region')}>
            {tiles.length > 0 ? (
                <ul className={`resource-tiles${error ? ' is-stale' : ''}`}>
                    {tiles.map((tile) => (
                        <li key={tile.key} className={`resource-tile tone-${tile.tone}`} title={tile.title}>
                            <Sparkline values={tile.history} max={tile.max} dashed={tile.dashed} />
                            <span className="resource-text">
                                <span className="resource-label">
                                    <bdi>{tile.label}</bdi>
                                    {tile.name && <span className="resource-sr-only">{` (${tile.name})`}</span>}
                                </span>
                                <span className="resource-reading">
                                    {tile.value && <bdi dir="ltr" className="resource-value">{tile.value}</bdi>}
                                    {tile.detail && <bdi dir="ltr" className="resource-detail">{tile.detail}</bdi>}
                                </span>
                            </span>
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="resource-status" role="status">{error ? t('resources.error') : t('resources.reading')}</p>
            )}
            {error && tiles.length > 0 && (
                <p className="resource-status is-error" role="status" title={error}>{t('resources.error')}</p>
            )}
            <button
                type="button"
                className="btn-icon-sm resource-close"
                onClick={onClose}
                aria-label={t('resources.hide')}
                title={t('resources.hide')}
            >
                <X size={14} aria-hidden="true" />
            </button>
        </section>
    );
}
