// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { GpuMemoryStatus, SetupCatalogEntry, SetupPlan, StartCard } from '@webui/types';

import { starterCards, summaryRows } from './readyView';

const cards = [{ skill: 'summarize' }, { skill: 'check-in' }] as StartCard[];

describe('starterCards', () => {
    it('leaves "Check in" out of a first run and offers it when setup is run again', () => {
        expect(starterCards(cards, false).map((c) => c.skill)).toEqual(['summarize']);
        expect(starterCards(cards, true).map((c) => c.skill)).toEqual(['summarize', 'check-in']);
    });
});

describe('summaryRows', () => {
    const entry = (e: Partial<SetupCatalogEntry>) => ({ id: 'm', name: 'M', roles: ['chat'], recipe: null, downloaded: true, gaia_model: true, planned: true, skipped: false, ...e }) as SetupCatalogEntry;
    const plan = (catalog: SetupCatalogEntry[]) => ({ catalog, vision_model: null }) as unknown as SetupPlan;

    it('says image reading is off when the vision model is not downloaded, and marks an NPU chat model', () => {
        const catalog = [
            entry({ id: 'chat', name: 'Chat', recipe: 'flm' }),
            entry({ id: 'vlm', name: 'Vision', roles: ['vision'], downloaded: false }),
        ];
        const rows = summaryRows(plan(catalog), 'chat', undefined);
        expect(rows).toContainEqual({ kind: 'chat', model: 'Chat', npu: true });
        expect(rows).toContainEqual({ kind: 'vision', model: 'Vision', downloaded: false });
    });

    it('lists what setup skipped and a GPU memory change waiting on a restart', () => {
        const catalog = [entry({ id: 'chat', name: 'Chat' }), entry({ id: 'x', name: 'Extra', roles: ['speech'], skipped: true })];
        const gpu = { gpu_gb: 16, pending: { state: 'took_effect', setting_gb: 24 } } as unknown as GpuMemoryStatus;
        const rows = summaryRows(plan(catalog), 'chat', gpu);
        expect(rows).toContainEqual({ kind: 'notInstalled', models: ['Extra'] });
        expect(rows).toContainEqual({ kind: 'gpuNow', gb: 16 });
    });
});
