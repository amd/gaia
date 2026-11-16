// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { initialSelection, resolveVision, totals } from '@webui/components/setup/selection';
import type { SetupCatalogEntry, SetupPlan } from '@webui/types';

import { saveModelChoices } from '@/data/setup';
import { createFakeClient } from '@/api/fake';

import { backendView, chooseBackend } from './modelsView';

const entry = (e: Partial<SetupCatalogEntry>): SetupCatalogEntry => ({
    id: 'm', name: 'M', roles: ['chat'], purpose: '', if_skipped: null, recipe: null, size_gb: 10, full_size_gb: 10,
    partial: false, partial_gb: 0, downloaded: false, in_lemonade: false, recommended: false, planned: true,
    gaia_model: true, selected: true, skipped: false, chat_choice: true, support: 'verified', reads_images: true,
    feature_tier: 'full', source: null, fits: true, need_gb: 12, capacity_gb: 64, fit_reason: null, ...e,
});

const gpuChat = entry({ id: 'gpu-chat', recommended: true });
const npuChat = entry({ id: 'npu-chat', recipe: 'flm', selected: false, planned: false, reads_images: false, size_gb: 9 });
const embed = entry({ id: 'embed', roles: ['embedding'], chat_choice: false, size_gb: 2 });
const vision = entry({ id: 'vision', roles: ['vision'], chat_choice: false, selected: false, planned: false, size_gb: 3 });

const plan = (catalog: SetupCatalogEntry[], npu?: SetupPlan['npu']): SetupPlan => ({
    lemonade: { mode: 'gaia', url: null, reachable: true },
    chat_model: { id: 'gpu-chat', user_set: false, size_gb: 10, downloaded: false },
    recommended_model: 'gpu-chat', decided_after_server: false, memory_gb: 64, memory_source: null, options: [], models: [],
    download_gb: 12, disk: { path: '/', free_gb: 100, source: 'local' }, catalog, npu,
});

describe('chooseBackend', () => {
    const p = plan([gpuChat, npuChat, embed, vision]);

    it('swaps the chat model for the NPU one, which turns image reading off, and back', () => {
        const start = initialSelection(p);
        const onNpu = chooseBackend(p, start, 'npu');
        expect(onNpu.defaultChat).toBe('npu-chat');
        expect(resolveVision(p.catalog!, onNpu)).toBeNull();
        expect(totals(p, onNpu).downloadGb).toBe(11);
        const back = chooseBackend(p, onNpu, 'gpu');
        expect(back.defaultChat).toBe('gpu-chat');
        expect(chooseBackend(p, start, 'gpu')).toBe(start);
    });

    it('offers no NPU choice while the NPU is not ready, and says why', () => {
        const problem = { code: 'driver' as const, title: 'x', fix: 'y', params: {} };
        const notReady = plan([gpuChat, npuChat], { present: true, ready: false, problems: [problem] });
        expect(backendView(notReady, initialSelection(notReady))).toMatchObject({ npuModel: undefined, npuProblems: [problem] });
        expect(chooseBackend(notReady, initialSelection(notReady), 'npu').defaultChat).toBe('gpu-chat');
    });
});

describe('saveModelChoices', () => {
    it('saves a changed chat model and nothing the PC already holds', async () => {
        const p = plan([gpuChat, npuChat, embed, vision]);
        const client = createFakeClient({ setupPlan: p });
        const selection = chooseBackend(p, initialSelection(p), 'npu');
        await saveModelChoices(client, p, selection, false);
        expect(client.setupChoices.map((c) => c.kind)).toEqual(['chat']);
        expect(client.setupChoices[0]).toEqual({ kind: 'chat', modelId: 'npu-chat', flag: false });

        const held = createFakeClient({ setupPlan: p });
        await saveModelChoices(held, p, initialSelection(p), false);
        expect(held.setupChoices).toEqual([]);
    });
});
