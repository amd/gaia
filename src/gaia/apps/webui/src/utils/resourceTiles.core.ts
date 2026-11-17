// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Translate } from '../i18n/translator';
import type { DeviceUsage, NetworkUsage, ResourceUsage } from '../types';

/** Samples a graph holds; the backend keeps the same minute of history. */
export const GRAPH_SLOTS = 60;

const GIB = 1024 ** 3;

/** Bytes as Task Manager shows them: binary gigabytes, one decimal, labelled GB. */
export function formatGb(bytes: number): string {
    return (bytes / GIB).toFixed(1);
}

export function pct(value: number): string {
    return `${Math.round(value)}%`;
}

/** Rates in bits per second, scaled the way Task Manager labels them. */
const RATE_UNITS: Array<[number, string]> = [[1e9, 'Gbps'], [1e6, 'Mbps'], [1e3, 'Kbps']];

/** The unit Task Manager would pick for a rate. */
export function rateUnit(bps: number): [number, string] {
    return RATE_UNITS.find(([size]) => bps >= size) ?? RATE_UNITS[RATE_UNITS.length - 1];
}

/** One rate in a given unit: "0", "8.0", "160". */
export function formatRate(bps: number, [size]: [number, string]): string {
    const v = bps / size;
    if (v < 0.05) return '0';
    return v >= 100 ? v.toFixed(0) : v.toFixed(1);
}

/** Graph ceiling for a rate history: the next 1, 2 or 5 step, at least 100 Kbps. */
export function rateScale(values: number[]): number {
    const peak = Math.max(100e3, ...values);
    const decade = 10 ** Math.floor(Math.log10(peak));
    const step = [1, 2, 5, 10].find((m) => peak <= m * decade)!;
    return step * decade;
}

export interface Tile {
    key: string;
    tone: 'cpu' | 'memory' | 'gpu' | 'npu' | 'network';
    label: string;
    value: string;
    detail?: string;
    /** The device's own name, for screen readers; sighted users get it on hover. */
    name?: string;
    title: string;
    history: number[];
    max?: number;
    dashed?: number[];
}

const KIND_LABELS = {
    gpu: 'resources.gpu',
    npu: 'resources.npu',
    ethernet: 'resources.ethernet',
    wifi: 'resources.wifi',
    network: 'resources.network',
} as const;

function kindLabel<T extends DeviceUsage | NetworkUsage>(t: Translate, item: T, all: T[]): string {
    const label = t(KIND_LABELS[item.kind]);
    const same = all.filter((d) => d.kind === item.kind);
    // Numbered from 0 the way Task Manager does, only when there are several.
    return same.length > 1 ? t('resources.numbered', { label, n: same.indexOf(item) }) : label;
}

function rate(bps: number): string {
    const unit = rateUnit(bps);
    return `${formatRate(bps, unit)} ${unit[1]}`;
}

/** A part that can't be read while CPU and memory still can: says so, with the reason on hover. */
function unavailable(t: Translate, tone: Tile['tone'], label: string, reason: string): Tile {
    return {
        key: `${tone}-unavailable`,
        tone,
        label,
        value: '',
        detail: t('resources.unavailable'),
        title: t('resources.unavailableWhy', { label, reason }),
        history: [],
    };
}

function isIdle(net: NetworkUsage): boolean {
    return [net.send_bps, net.recv_bps, ...net.send_history, ...net.recv_history].every((v) => v === 0);
}

/** Adapters that carried traffic this minute; when none did, the first so Network still shows. */
function shownNetworks(all: NetworkUsage[]): NetworkUsage[] {
    const busy = all.filter((n) => !isIdle(n));
    return busy.length > 0 ? busy : all.slice(0, 1);
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
            label: kindLabel(t, dev, devices),
            value: pct(dev.percent),
            detail: mem,
            name: dev.name,
            title: [dev.name, pct(dev.percent), mem && t('resources.inUse', { size: mem })].filter(Boolean).join(' · '),
            history: dev.history,
        });
    }
    if (usage.devices_error) tiles.push(unavailable(t, 'gpu', t('resources.gpu'), usage.devices_error));
    if (usage.npus_error) tiles.push(unavailable(t, 'npu', t('resources.npu'), usage.npus_error));
    const networks = shownNetworks(usage.networks ?? []);
    for (const net of networks) {
        // One unit for both, the larger one's, as Task Manager shows "S: 0 R: 8.0 Kbps".
        const unit = rateUnit(Math.max(net.send_bps, net.recv_bps));
        const send = formatRate(net.send_bps, unit);
        const recv = formatRate(net.recv_bps, unit);
        tiles.push({
            key: net.id,
            tone: 'network',
            label: kindLabel(t, net, networks),
            value: '',
            detail: t('resources.sendRecv', { send, recv, unit: unit[1] }),
            name: net.name,
            title: [
                net.name,
                net.description,
                t('resources.send', { rate: rate(net.send_bps) }),
                t('resources.receive', { rate: rate(net.recv_bps) }),
            ].join(' · '),
            history: net.recv_history,
            dashed: net.send_history,
            max: rateScale([...net.recv_history, ...net.send_history]),
        });
    }
    if (usage.networks_error) tiles.push(unavailable(t, 'network', t('resources.network'), usage.networks_error));
    return tiles;
}
