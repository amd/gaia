// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pure helpers for the Settings model list, shared by the web UI and the phone app. */

import type { MessageKey } from '../i18n/registry';
import type { Translate } from '../i18n/translator';
import type { ProviderModel } from '../types';

/** Same local model: Lemonade lists a registered `user.X` as `X`, in any case. */
export function sameModelId(a: string | null | undefined, b: string | null | undefined): boolean {
    if (!a || !b) return false;
    const bare = (id: string) => id.toLowerCase().replace(/^user\./, '');
    return bare(a) === bare(b);
}

/** The model id without its cloud provider prefix ("fireworks." or "amd."). */
function stripProviderPrefix(model: string): string {
    return /^(fireworks|amd)\./.test(model) ? model.slice(model.indexOf('.') + 1) : model;
}

/** The model name without its provider prefix or catalogue path. */
export function shortModelName(model: string): string {
    const withoutProvider = stripProviderPrefix(model);
    return withoutProvider.slice(withoutProvider.lastIndexOf('/') + 1);
}

/**
 * A label per model for one list, keyed by id. Names already unique are kept;
 * models that would share one show as much of their id as tells them apart.
 */
export function distinctLabels(
    models: Array<{ id: string; display_name?: string | null }>,
    nameOf: (id: string) => string = shortModelName,
): Record<string, string> {
    const labels: Record<string, string> = {};
    for (const m of models) labels[m.id] = m.display_name || nameOf(m.id);
    const byLabel = new Map<string, string[]>();
    for (const m of models) byLabel.set(labels[m.id], [...(byLabel.get(labels[m.id]) ?? []), m.id]);
    for (const ids of byLabel.values()) {
        if (ids.length < 2) continue;
        const paths = ids.map((id) => stripProviderPrefix(id).split('/'));
        let tails = ids;
        for (let n = 1; n <= Math.max(...paths.map((p) => p.length)); n++) {
            const candidate = paths.map((p) => p.slice(-n).join('/'));
            if (new Set(candidate).size === candidate.length) { tails = candidate; break; }
        }
        ids.forEach((id, i) => { labels[id] = tails[i]; });
    }
    return labels;
}

export interface ModelGroups {
    /** Models matching the search, before any grouping. */
    filtered: ProviderModel[];
    /** Local: runnable and on the PC, the one in use first. */
    onThisPc: ProviderModel[];
    /** Local: runnable but not downloaded. Remote: every runnable model shown. */
    toDownload: ProviderModel[];
    /** Runnable models shown, in list order. */
    visibleRunnable: ProviderModel[];
    unavailable: ProviderModel[];
    /** Fireworks shows its ranked picks until "Show all models". */
    collapsed: boolean;
    hiddenCount: number;
}

/** What the list shows for a search: runnable models, those hidden by the recommended view, and those the server says can't run. */
export function groupModels(
    models: ProviderModel[] | null | undefined,
    opts: { query: string; provider: string; showAll: boolean; activeModel: string | null | undefined },
): ModelGroups {
    const q = opts.query.trim().toLowerCase();
    const filtered = (models ?? []).filter(
        (m) => !q || m.id.toLowerCase().includes(q) || (m.display_name ?? '').toLowerCase().includes(q),
    );
    const runnable = filtered.filter((m) => m.available !== false);
    const collapsed = opts.provider === 'fireworks' && !opts.showAll && !q && runnable.some((m) => m.rank !== null);
    const inCollapsedView = (m: ProviderModel) => m.rank !== null || m.id === opts.activeModel;
    const visibleRunnable = collapsed ? runnable.filter(inCollapsedView) : runnable;
    const isCurrent = (m: ProviderModel) => sameModelId(opts.activeModel, m.id);
    return {
        filtered,
        visibleRunnable,
        onThisPc: visibleRunnable.filter((m) => m.downloaded).sort((a, b) => Number(isCurrent(b)) - Number(isCurrent(a))),
        toDownload: visibleRunnable.filter((m) => !m.downloaded),
        unavailable: filtered.filter((m) => m.available === false),
        collapsed,
        hiddenCount: collapsed ? runnable.filter((m) => !inCollapsedView(m)).length : 0,
    };
}

// The backend's recommendation notes and evidence (gaia/llm/recommended_models.py), in English.
export const RANK_NOTES: Record<string, MessageKey> = {
    'best overall, fastest': 'settings.model.note.bestFastest',
    cheapest: 'settings.model.note.cheapest',
    'most truthful': 'settings.model.note.mostTruthful',
};
const EVIDENCE = /^(\d+\/\d+) tasks · quality ([\d.]+\/5) · (\$[\d.]+) per (\d+)-task run$/;

/** "Measured: …" in the chosen language; evidence worded differently from ours shows as sent. */
export function measuredText(t: Translate, evidence: string): string {
    const parts = EVIDENCE.exec(evidence);
    return t.compose((say) => say('settings.model.measured', {
        evidence: parts ? say('settings.model.evidence', { tasks: parts[1], quality: parts[2], cost: parts[3], n: parts[4] }) : evidence,
    }));
}

// "1M" reads at a glance; "1,048,576" has to be counted.
const compactCount = (n: number): string =>
    new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 0 }).format(n);

/** Where it runs, its size, speed and context, as one line; empty when there is nothing to say. */
export function modelFacts(t: Translate, m: ProviderModel): string {
    const placement = m.placement?.label;
    return t.compose((say) => [
        placement && (m.placement?.source === 'expected' ? say('settings.model.expected', { label: placement }) : placement),
        m.size_gb ? say(m.downloaded ? 'settings.model.sizeOnDisk' : 'settings.model.sizeDownload', { size: `${m.size_gb} GB` }) : null,
        m.tokens_per_second ? say('settings.model.speed', { speed: `${Math.round(m.tokens_per_second)} tok/s` }) : null,
        !m.placement && m.context_length ? say('settings.model.contextTokens', { size: compactCount(m.context_length) }) : null,
    ].filter(Boolean).join(' · '));
}
