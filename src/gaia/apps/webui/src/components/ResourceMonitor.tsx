// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { X } from 'lucide-react';
import { useResourceUsage } from '../hooks/useResourceUsage';
import { useT, type Translate } from '../i18n/useT';
import type { DeviceUsage, ResourceUsage } from '../types';
import './ResourceMonitor.css';

/** Samples a graph holds; the backend keeps the same minute of history. */
export const GRAPH_SLOTS = 60;

const GIB = 1024 ** 3;

/** Bytes as Task Manager shows them: binary gigabytes, one decimal, labelled GB. */
export function formatGb(bytes: number): string {
    return (bytes / GIB).toFixed(1);
}

function pct(value: number): string {
    return `${Math.round(value)}%`;
}

/** Plot inset so a flat 0% or 100% line stays clear of the box's border. */
const INSET = 4;

/** An area graph of the last minute, newest on the right, 0–100. */
export function Sparkline({ values }: { values: number[] }) {
    const pts = values.slice(-GRAPH_SLOTS);
    const offset = GRAPH_SLOTS - pts.length;
    const xy = pts.map((v, i) => [
        ((offset + i) / (GRAPH_SLOTS - 1)) * 100,
        100 - INSET - (Math.max(0, Math.min(100, v)) * (100 - 2 * INSET)) / 100,
    ]);
    const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
    const area = xy.length > 1 ? `${line} L100 100 L${xy[0][0].toFixed(2)} 100 Z` : '';
    return (
        <svg className="resource-spark" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {area && <path className="resource-spark-area" d={area} />}
            {xy.length > 1 && <path className="resource-spark-line" d={line} vectorEffect="non-scaling-stroke" />}
        </svg>
    );
}

interface Tile {
    key: string;
    tone: 'cpu' | 'memory' | 'gpu' | 'npu';
    label: string;
    value: string;
    detail?: string;
    /** The device's own name, for screen readers; sighted users get it on hover. */
    name?: string;
    title: string;
    history: number[];
}

function deviceLabel(t: Translate, dev: DeviceUsage, all: DeviceUsage[]): string {
    const label = t(dev.kind === 'npu' ? 'resources.npu' : 'resources.gpu');
    const same = all.filter((d) => d.kind === dev.kind);
    // Numbered from 0 the way Task Manager does, only when there are several.
    return same.length > 1 ? t('resources.numbered', { label, n: same.indexOf(dev) }) : label;
}

export function buildTiles(t: Translate, usage: ResourceUsage): Tile[] {
    const tiles: Tile[] = [];
    if (usage.cpu) {
        const ghz = usage.cpu.ghz != null ? `${usage.cpu.ghz.toFixed(2)} GHz` : undefined;
        tiles.push({
            key: 'cpu',
            tone: 'cpu',
            label: t('resources.cpu'),
            value: pct(usage.cpu.percent),
            detail: ghz,
            title: [t('resources.cpu'), pct(usage.cpu.percent), ghz].filter(Boolean).join(' · '),
            history: usage.cpu.history,
        });
    }
    if (usage.memory) {
        const m = usage.memory;
        const size = `${formatGb(m.used_bytes)}/${formatGb(m.total_bytes)} GB`;
        tiles.push({
            key: 'memory',
            tone: 'memory',
            label: t('resources.memory'),
            value: pct(m.percent),
            detail: size,
            title: `${t('resources.memory')} · ${size} (${pct(m.percent)})`,
            history: m.history,
        });
    }
    const devices = usage.devices ?? [];
    for (const dev of devices) {
        const mem = dev.memory_used_bytes != null ? `${formatGb(dev.memory_used_bytes)} GB` : undefined;
        tiles.push({
            key: dev.id,
            tone: dev.kind,
            label: deviceLabel(t, dev, devices),
            value: pct(dev.percent),
            detail: mem,
            name: dev.name,
            title: [dev.name, pct(dev.percent), mem && t('resources.inUse', { size: mem })].filter(Boolean).join(' · '),
            history: dev.history,
        });
    }
    return tiles;
}

interface ResourceMonitorProps {
    onClose: () => void;
}

/** This PC's CPU, memory, GPU and NPU load as a strip of live graphs. */
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
                            <Sparkline values={tile.history} />
                            <span className="resource-text">
                                <span className="resource-label">
                                    {tile.label}
                                    {tile.name && <span className="resource-sr-only">{` (${tile.name})`}</span>}
                                </span>
                                <span className="resource-reading">
                                    <bdi dir="ltr" className="resource-value">{tile.value}</bdi>
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
