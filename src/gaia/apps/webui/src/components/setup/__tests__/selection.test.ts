// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import {
    chatCandidates, extraModels, initialSelection, installedOnlyRows, mainRows, moreRows, overallFromPlan, recommendedSelection,
    chooseChat, shortfall, showGpuStep, skipChanges, toggle, tooBig, totals, visionSkipped,
} from '../selection';
import { COLD_BIG, EMBED, FLM, GEMMA, GPT_OSS, QWEN, entry, gpuStatus, plan, progress, runStatus } from './fixtures';
import coldStrixHalo from './coldStrixHalo.json';
import type { GpuMemoryStatus, SetupPlan } from '../../../types';

describe('the selection a plan starts from', () => {
    it('ticks what setup plans, so the recommended set is preselected', () => {
        const sel = initialSelection(COLD_BIG());
        expect([...sel.ticked].sort()).toEqual([EMBED.id, GEMMA.id, QWEN.id].sort());
        expect(sel.defaultChat).toBe(QWEN.id);
    });

    it("ticks GAIA's models already on disk, but not a skipped one", () => {
        const onDisk = entry({ id: 'Qwen3-VL-4B-Instruct-GGUF', downloaded: true, size_gb: 0 });
        const skipped = { ...GEMMA, skipped: true, selected: false, downloaded: true, size_gb: 0 };
        const sel = initialSelection(plan([QWEN, skipped, EMBED, onDisk]));
        expect(sel.ticked.has(onDisk.id)).toBe(true);
        expect(sel.ticked.has(GEMMA.id)).toBe(false);
    });

    it("splits GAIA's planned models, its other ones, and what only Lemonade has", () => {
        const catalog = [QWEN, GEMMA, EMBED, GPT_OSS, FLM];
        expect(mainRows(catalog).map((e) => e.id)).toEqual([QWEN.id, GEMMA.id, EMBED.id]);
        expect(moreRows(catalog).map((e) => e.id)).toEqual([GPT_OSS.id]);
        expect(installedOnlyRows(catalog).map((e) => e.id)).toEqual([FLM.id]);
    });
});

describe('totals against the model drive', () => {
    it('adds up only what is left to download, and what remains free after', () => {
        const p = COLD_BIG();
        expect(totals(p, initialSelection(p))).toEqual({ downloadGb: 29.6, freeGb: 412.3, afterGb: 382.7 });
    });

    it('updates on every tick', () => {
        const p = COLD_BIG();
        const sel = toggle(initialSelection(p), p.catalog!, QWEN.id, false);
        expect(totals(p, sel).downloadGb).toBe(6.3);
        expect(totals(p, toggle(sel, p.catalog!, GPT_OSS.id, true)).downloadGb).toBe(18.4);
    });

    it('never invents a size or a free-space number', () => {
        const unknownSize = { ...GPT_OSS, size_gb: null, selected: true, planned: true };
        const p = plan([EMBED, unknownSize], { disk: { path: null, free_gb: null, source: null } });
        const tt = totals(p, initialSelection(p));
        expect(tt).toEqual({ downloadGb: null, freeGb: null, afterGb: null });
        expect(shortfall(p, initialSelection(p))).toBeNull();
    });

    it('names how much to free and the single model whose skip makes it fit', () => {
        const p = plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 11.2, source: 'lemonade' } });
        expect(shortfall(p, initialSelection(p))).toMatchObject({ needGb: 29.6, freeGb: 11.2, freeUpGb: 18.4, drop: { id: QWEN.id } });
    });

    it('names no model when no single skip is enough', () => {
        const p = plan([QWEN, GEMMA, EMBED], { disk: { path: 'D:\\hub', free_gb: 2, source: 'lemonade' } });
        expect(shortfall(p, initialSelection(p))?.drop).toBeNull();
    });
});

describe('ticking and the default chat model', () => {
    it('moves the default to another usable chat model when its model is unticked', () => {
        const p = COLD_BIG();
        const sel = toggle(initialSelection(p), p.catalog!, QWEN.id, false);
        expect(sel.defaultChat).toBe(GEMMA.id);
        expect(sel.unticked.has(QWEN.id)).toBe(true);
    });

    it('leaves no default when no chat model is ticked or installed', () => {
        const p = COLD_BIG();
        let sel = toggle(initialSelection(p), p.catalog!, QWEN.id, false);
        sel = toggle(sel, p.catalog!, GEMMA.id, false);
        expect(sel.defaultChat).toBeNull();
    });

    it('keeps an installed FLM model as a chat choice with nothing ticked', () => {
        const p = plan([QWEN, GEMMA, EMBED, FLM]);
        let sel = toggle(initialSelection(p), p.catalog!, QWEN.id, false);
        sel = toggle(sel, p.catalog!, GEMMA.id, false);
        expect(chatCandidates(p.catalog!, sel.ticked).map((e) => e.id)).toEqual([FLM.id]);
        expect(sel.defaultChat).toBe(FLM.id);
    });

    it('resets to the recommended set', () => {
        const p = COLD_BIG();
        const sel = recommendedSelection(p);
        expect([...sel.ticked].sort()).toEqual([EMBED.id, GEMMA.id, QWEN.id].sort());
        expect(sel.defaultChat).toBe(QWEN.id);
    });

    it('reports an unticked vision model, the caveat being a caveat, not a block', () => {
        const p = COLD_BIG();
        expect(visionSkipped(p.catalog!, toggle(initialSelection(p), p.catalog!, GEMMA.id, false))?.id).toBe(GEMMA.id);
        expect(visionSkipped(p.catalog!, initialSelection(p))).toBeNull();
    });

    it('lists ticked models that may not fit memory', () => {
        const big = { ...QWEN, fits: false, need_gb: 27.1, capacity_gb: 16 };
        const p = plan([big, GEMMA, EMBED]);
        expect(tooBig(p.catalog!, initialSelection(p)).map((e) => e.id)).toEqual([QWEN.id]);
    });
});

describe('turning the selection into choices', () => {
    it('skips unticked planned models and never skips the chat model in use', () => {
        const p = COLD_BIG();
        let sel = toggle(initialSelection(p), p.catalog!, GEMMA.id, false);
        sel = { ...sel, ticked: new Set([...sel.ticked].filter((id) => id !== QWEN.id)) };
        expect(skipChanges(p, sel)).toEqual([{ id: GEMMA.id, skipped: true }]);
    });

    it('takes a ticked model back off the skip list', () => {
        const skipped = { ...GEMMA, skipped: true, selected: false };
        const p = plan([QWEN, skipped, EMBED]);
        const sel = { ...initialSelection(p), ticked: new Set([QWEN.id, GEMMA.id, EMBED.id]) };
        expect(skipChanges(p, sel)).toEqual([{ id: GEMMA.id, skipped: false }]);
    });

    it("pulls ticked models setup doesn't plan beside the plan's", () => {
        const p = COLD_BIG();
        expect(extraModels(p, toggle(initialSelection(p), p.catalog!, GPT_OSS.id, true))).toEqual([GPT_OSS.id]);
        expect(extraModels(p, initialSelection(p))).toEqual([]);
    });
});

describe('the GPU memory step', () => {
    it('shows on an integrated GPU where a bigger share makes a model fit', () => {
        const p = COLD_BIG();
        expect(showGpuStep(gpuStatus(), initialSelection(p))).toBe(true);
    });

    it('hides on a discrete or unknown GPU, or when nothing new would fit', () => {
        const sel = initialSelection(COLD_BIG());
        expect(showGpuStep(gpuStatus({ applies: false }), sel)).toBe(false);
        expect(showGpuStep(gpuStatus({ show_in_setup: false }), sel)).toBe(false);
        expect(showGpuStep(null, sel)).toBe(false);
    });

    it('hides once the user unticked every model it would help', () => {
        const p = COLD_BIG();
        const only = gpuStatus({ presets: [{ setting_gb: 64, gpu_gb: 64, unlocks: [QWEN.id] }] });
        expect(showGpuStep(only, toggle(initialSelection(p), p.catalog!, QWEN.id, false))).toBe(false);
    });
});

describe('the GPU step only for a model the user picked', () => {
    it('stays hidden when the model a bigger share would help was never ticked', () => {
        // An NPU-only user: the 23 GB model is not theirs, so no step promotes it.
        const p = plan([{ ...QWEN, recommended: false, planned: false, selected: false }, GEMMA, EMBED, FLM], {
            chat_model: { id: FLM.id, user_set: true, size_gb: 0, downloaded: true },
        });
        const sel = initialSelection(p);
        expect(sel.defaultChat).toBe(FLM.id);
        expect(sel.ticked.has(QWEN.id)).toBe(false);
        expect(showGpuStep(gpuStatus({ presets: [{ setting_gb: 64, gpu_gb: 64, unlocks: [QWEN.id] }] }), sel)).toBe(false);
    });

    it('shows once the user ticks that model', () => {
        const p = plan([{ ...QWEN, recommended: false, planned: false, selected: false }, GEMMA, EMBED]);
        const sel = toggle(initialSelection(p), p.catalog!, QWEN.id, true);
        expect(showGpuStep(gpuStatus({ presets: [{ setting_gb: 64, gpu_gb: 64, unlocks: [QWEN.id] }] }), sel)).toBe(true);
    });
});

describe('download totals from one source', () => {
    it('counts only what was left to download, as the models step did', () => {
        // The embedder is on disk; Qwen was 7.6 GB in; Gemma is new.
        const p = plan([
            { ...QWEN, partial: true, partial_gb: 7.6, size_gb: 15.7 },
            GEMMA,
            { ...EMBED, downloaded: true, size_gb: 0 },
        ]);
        const sel = initialSelection(p);
        const status = runStatus([
            progress({ id: EMBED.id, roles: ['embedding'], state: 'verifying', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
            progress({ id: QWEN.id, state: 'downloading', bytes_done: 9.6e9, bytes_total: 23.3e9 }),
            progress({ id: GEMMA.id, roles: ['vision'], state: 'queued', bytes_total: 5.97e9 }),
        ]);
        const overall = overallFromPlan(status, p)!;
        expect(overall.bytes_total / 1e9).toBeCloseTo(totals(p, sel).downloadGb!, 1);
        // 2 GB of Qwen's remaining 15.7 GB have come since the start.
        expect(overall.bytes_done / 1e9).toBeCloseTo(2.0, 1);
        expect(overall.percent).toBe(Math.floor((2.0 / 21.67) * 100));
    });

    it('reads 100% when everything was already on this PC', () => {
        const p = plan([{ ...GEMMA, downloaded: true, size_gb: 0 }, { ...EMBED, downloaded: true, size_gb: 0 }]);
        const status = runStatus([progress({ id: GEMMA.id, state: 'verifying', bytes_done: 6e9, bytes_total: 6e9 })], { state: 'verifying' });
        expect(overallFromPlan(status, p)!.percent).toBe(100);
    });
});

describe('the GPU step on a real cold start', () => {
    // Read from the real /api/setup/plan and /api/gpu-memory: a Strix Halo with a
    // 16 GB GPU share and nothing downloaded. Setup plans Gemma; Qwen doesn't fit yet.
    const cold = coldStrixHalo as unknown as { plan: SetupPlan; gpu: GpuMemoryStatus };

    it('offers a bigger share for the default model, though setup left it unticked', () => {
        const sel = initialSelection(cold.plan);
        expect(sel.ticked.has(QWEN.id)).toBe(false);
        expect(sel.defaultChat).toBe(GEMMA.id);
        expect(showGpuStep(cold.gpu, sel)).toBe(true);
    });

    it('hides it once the user unticks that model', () => {
        const catalog = cold.plan.catalog!;
        const sel = toggle(toggle(initialSelection(cold.plan), catalog, QWEN.id, true), catalog, QWEN.id, false);
        expect(showGpuStep(cold.gpu, sel)).toBe(false);
    });

    it('hides it once the user picks their own chat model, unless a bigger share helps that one', () => {
        const catalog = cold.plan.catalog!;
        expect(showGpuStep(cold.gpu, chooseChat(initialSelection(cold.plan), catalog, GEMMA.id))).toBe(false);
        expect(showGpuStep(cold.gpu, chooseChat(initialSelection(cold.plan), catalog, QWEN.id))).toBe(true);
    });

    it('never offers it for a non-default model the user did not tick', () => {
        const gpu = { ...cold.gpu, models: cold.gpu.models!.filter((m) => !m.default) };
        expect(gpu.models.some((m) => !m.fits_now && m.fits_at_setting_gb != null)).toBe(true);
        expect(showGpuStep(gpu, initialSelection(cold.plan))).toBe(false);
    });

    it('ignores a model that already fits or that no share would fit', () => {
        const sel = toggle(initialSelection(cold.plan), cold.plan.catalog!, GPT_OSS.id, true);
        const gpu = { ...cold.gpu, models: cold.gpu.models!.filter((m) => m.fits_now || m.fits_at_setting_gb == null) };
        expect(showGpuStep(gpu, sel)).toBe(false);
    });
});
