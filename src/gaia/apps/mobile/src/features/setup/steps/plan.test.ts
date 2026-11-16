// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { initialSelection } from '@webui/components/setup/selection';
import type { PreflightReport, SetupCatalogEntry, SetupPlan } from '@webui/types';
import type { ProviderSummary } from '@webui/types/api.gen';

import { cloudIsBlocked, planView, startOptions } from './plan';

const entry = (e: Partial<SetupCatalogEntry>): SetupCatalogEntry => ({
    id: 'm', name: 'M', roles: ['chat'], purpose: '', if_skipped: null, recipe: null, size_gb: 10, full_size_gb: 10,
    partial: false, partial_gb: 0, downloaded: false, in_lemonade: false, recommended: false, planned: true,
    gaia_model: true, selected: true, skipped: false, chat_choice: true, support: 'verified', reads_images: false,
    feature_tier: 'full', source: null, fits: true, need_gb: 12, capacity_gb: 64, fit_reason: null, ...e,
});

const plan = (catalog: SetupCatalogEntry[], free: number | null): SetupPlan => ({
    lemonade: { mode: 'gaia', url: null, reachable: true },
    chat_model: { id: 'chat', user_set: false, size_gb: 10, downloaded: false },
    recommended_model: 'chat', decided_after_server: false, memory_gb: 64, memory_source: null, options: [], models: [],
    download_gb: 12, disk: { path: '/', free_gb: free, source: 'local' }, catalog,
});

const chat = entry({ id: 'chat', size_gb: 10 });
const embed = entry({ id: 'embed', roles: ['embedding'], chat_choice: false, size_gb: 2 });
const view = (p: SetupPlan, pre?: PreflightReport) => planView(p, initialSelection(p), pre);

describe('planView', () => {
    it('adds up what will download and the space left after it', () => {
        const v = view(plan([chat, embed], 100));
        expect(v.pending.map((e) => e.id)).toEqual(['chat', 'embed']);
        expect(v.totals).toMatchObject({ downloadGb: 12, afterGb: 88 });
        expect(v.short).toBeNull();
    });

    it('says how much to free when the disk is short', () => {
        expect(view(plan([chat, embed], 5)).short).toMatchObject({ needGb: 12, freeGb: 5, freeUpGb: 7 });
    });

    it('names a bigger verified model the memory cannot hold, but never the chosen one', () => {
        const big = entry({ id: 'big', fits: false, fit_code: 'memory', planned: false, selected: false, recommended: false });
        expect(view(plan([chat, embed, big], 100)).bigger?.id).toBe('big');
        expect(view(plan([chat, embed], 100)).bigger).toBeUndefined();
    });

    it('is blocked only when the PC reports blockers and is not compatible', () => {
        const p = plan([chat], 100);
        const pre = { compatible: false, blockers: ['No 64-bit processor'] } as PreflightReport;
        expect(view(p, pre)).toMatchObject({ blocked: true, blockers: ['No 64-bit processor'] });
        expect(view(p, { ...pre, compatible: true }).blocked).toBe(false);
        expect(view(p).blocked).toBe(false);
    });

    it('runs on the NPU for an NPU model, else on the graphics chip when it is known', () => {
        const npu = entry({ id: 'chat', recipe: 'flm' });
        expect(view(plan([npu], 100)).onNpu).toBe(true);
        expect(view(plan([chat], 100), { gpu_name: 'Radeon' } as PreflightReport)).toMatchObject({ onNpu: false, gpuName: 'Radeon' });
    });
});

describe('cloudIsBlocked', () => {
    const provider = (id: string, blocked: boolean) => ({ id, blocked }) as ProviderSummary;
    it('blocks the cloud card only while Local only blocks every cloud provider', () => {
        expect(cloudIsBlocked([provider('local', false), provider('fireworks', true), provider('amd', true)])).toBe(true);
        expect(cloudIsBlocked([provider('fireworks', true), provider('amd', false)])).toBe(false);
        expect(cloudIsBlocked(undefined)).toBe(false);
    });
});

describe('startOptions', () => {
    it('starts with the ticked models setup does not plan, and the confirmed low disk', () => {
        const extra = entry({ id: 'extra', planned: false, selected: false, chat_choice: false });
        const p = plan([chat, extra], 100);
        const selection = initialSelection(p);
        const ticked = { ...selection, ticked: new Set([...selection.ticked, 'extra']) };
        expect(startOptions(p, ticked, true, true)).toEqual({ acknowledgeLowDisk: true, keepTrying: true, extraModels: ['extra'] });
        expect(startOptions(p, selection, false, false).extraModels).toEqual([]);
    });
});
