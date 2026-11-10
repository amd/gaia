// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, expect, it } from 'vitest';
import {
    chatCandidates, extraModels, initialSelection, installedOnlyRows, mainRows, moreRows, overallFromPlan, recommendedSelection,
    chooseChat, pendingDownloads, pickChat, pickVision, resolveVision, savedVision, shortfall, skipChanges,
    toggle, tooBig, totals, visionSkipped, VISION_NONE,
} from '../selection';
import {
    COLD_BIG, EMBED, FLM, GEMMA, GEMMA_CHAT, GPT_OSS, QWEN, QWEN_READS, SDXL, entry, gpuStatus, plan, progress, runStatus,
} from './fixtures';
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

    it('takes a removed model off the skip list when it is ticked again, so it downloads', () => {
        // Removed in Settings: on the skip list, and not one setup plans.
        const removed = { ...GPT_OSS, skipped: true, downloaded: false };
        const p = plan([QWEN, GEMMA, EMBED, removed]);
        const sel = toggle(initialSelection(p), p.catalog!, GPT_OSS.id, true);
        expect(skipChanges(p, sel)).toEqual([{ id: GPT_OSS.id, skipped: false }]);
        expect(extraModels(p, sel)).toEqual([GPT_OSS.id]);
        // Left unticked, it stays skipped.
        expect(skipChanges(p, initialSelection(p))).toEqual([]);
    });

    it("pulls ticked models setup doesn't plan beside the plan's", () => {
        const p = COLD_BIG();
        expect(extraModels(p, toggle(initialSelection(p), p.catalog!, GPT_OSS.id, true))).toEqual([GPT_OSS.id]);
        expect(extraModels(p, initialSelection(p))).toEqual([]);
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


describe('picking the chat model, and the vision model that follows it', () => {
    const OTHER_VISION = entry({ id: 'Qwen3-VL-4B-Instruct-GGUF', name: 'Qwen3 VL 4B', roles: ['vision'], chat_choice: false, gaia_model: false, size_gb: 3 });
    const P = () => plan([QWEN_READS, GEMMA_CHAT, EMBED, GPT_OSS, SDXL, OTHER_VISION]);
    const pending = (sel: ReturnType<typeof initialSelection>, p = P()) => pendingDownloads(p.catalog!, sel).map((e) => e.id);

    it("doesn't download Gemma when the chat model reads images itself", () => {
        const p = P();
        const sel = initialSelection(p);
        expect(resolveVision(p.catalog!, sel)).toBe(QWEN.id);
        expect(pending(sel)).not.toContain(GEMMA.id);
        expect(sel.unticked.has(GEMMA.id)).toBe(true);
    });

    it('preselects Gemma when the new chat model has no vision', () => {
        const p = P();
        const sel = pickChat(initialSelection(p), p.catalog!, GPT_OSS.id);
        expect(sel.defaultChat).toBe(GPT_OSS.id);
        expect(sel.chatChosen).toBe(true);
        expect(resolveVision(p.catalog!, sel)).toBe(GEMMA.id);
        expect(pending(sel)).toEqual(expect.arrayContaining([GPT_OSS.id, GEMMA.id]));
    });

    it('stops downloading the chat model it replaces, but keeps one on disk ticked', () => {
        const p = P();
        expect(pending(pickChat(initialSelection(p), p.catalog!, GPT_OSS.id))).not.toContain(QWEN.id);
        const onDisk = plan([{ ...QWEN_READS, downloaded: true, size_gb: 0 }, GEMMA_CHAT, EMBED, GPT_OSS]);
        expect(pickChat(initialSelection(onDisk), onDisk.catalog!, GPT_OSS.id).ticked.has(QWEN.id)).toBe(true);
    });

    it('drops Gemma again when the user comes back to a chat model that reads images', () => {
        const p = P();
        const away = pickChat(initialSelection(p), p.catalog!, GPT_OSS.id);
        const back = pickChat(away, p.catalog!, QWEN.id);
        expect(pending(back)).not.toContain(GEMMA.id);
        expect(pending(back)).not.toContain(GPT_OSS.id);
    });

    it('needs no second vision model when Gemma is the chat model', () => {
        const p = P();
        const sel = pickChat(initialSelection(p), p.catalog!, GEMMA.id);
        expect(resolveVision(p.catalog!, sel)).toBe(GEMMA.id);
        expect(pending(sel).filter((id) => id === GEMMA.id)).toHaveLength(1);
    });

    it("unticks image creation for a compact chat model, and doesn't put it back for a full one", () => {
        const p = P();
        const compact = pickChat(toggle(initialSelection(p), p.catalog!, SDXL.id, true), p.catalog!, GEMMA.id);
        expect(compact.ticked.has(SDXL.id)).toBe(false);
        expect(pickChat(compact, p.catalog!, QWEN.id).ticked.has(SDXL.id)).toBe(false);
    });

    it('keeps image creation ticked for a full chat model', () => {
        const p = P();
        const sel = pickChat(toggle(initialSelection(p), p.catalog!, SDXL.id, true), p.catalog!, GPT_OSS.id);
        expect(sel.ticked.has(SDXL.id)).toBe(true);
    });

    it('Skip for now drops the vision model from the download', () => {
        const p = P();
        const chat = pickChat(initialSelection(p), p.catalog!, GPT_OSS.id);
        const sel = pickVision(chat, p.catalog!, VISION_NONE);
        expect(resolveVision(p.catalog!, sel)).toBeNull();
        expect(pending(sel)).not.toContain(GEMMA.id);
        expect(sel.unticked.has(GEMMA.id)).toBe(true);
    });

    it('keeps a vision model on disk when it stops being the pick', () => {
        const p = plan([QWEN_READS, { ...GEMMA_CHAT, downloaded: true, size_gb: 0 }, EMBED, OTHER_VISION]);
        expect(pickVision(initialSelection(p), p.catalog!, OTHER_VISION.id).ticked.has(GEMMA.id)).toBe(true);
    });

    it("swaps the download when the user picks another vision model, and back on GAIA's choice", () => {
        const p = plan([GPT_OSS, GEMMA, EMBED, OTHER_VISION], { chat_model: { id: GPT_OSS.id, user_set: true, size_gb: 12.1, downloaded: false } });
        const other = pickVision(initialSelection(p), p.catalog!, OTHER_VISION.id);
        expect(pending(other, p)).toContain(OTHER_VISION.id);
        expect(pending(other, p)).not.toContain(GEMMA.id);
        const back = pickVision(other, p.catalog!, null);
        expect(pending(back, p)).toContain(GEMMA.id);
        expect(pending(back, p)).not.toContain(OTHER_VISION.id);
    });

    it('starts from the saved vision model, and treats a skipped one as Skip for now', () => {
        const chat = { id: GPT_OSS.id, user_set: true, size_gb: 12.1, downloaded: false };
        const chosen = plan([GPT_OSS, GEMMA, OTHER_VISION], { vision_model: OTHER_VISION.id, chat_model: chat });
        expect(resolveVision(chosen.catalog!, initialSelection(chosen))).toBe(OTHER_VISION.id);
        const skipped = plan([GPT_OSS, { ...GEMMA, skipped: true, selected: false }], { chat_model: chat });
        expect(initialSelection(skipped).vision).toBe(VISION_NONE);
    });
});

describe('what is saved as vision_model', () => {
    const OTHER_VISION = entry({ id: 'Qwen3-VL-4B-Instruct-GGUF', roles: ['vision'], chat_choice: false, gaia_model: false });
    const P = () => plan([QWEN_READS, GEMMA_CHAT, EMBED, GPT_OSS, OTHER_VISION]);

    it('saves nothing when the chat model does the job', () => {
        const p = P();
        expect(savedVision(p.catalog!, initialSelection(p))).toBeNull();
    });

    it("saves nothing for GAIA's own vision model beside a chat model without vision", () => {
        const p = P();
        expect(savedVision(p.catalog!, pickChat(initialSelection(p), p.catalog!, GPT_OSS.id))).toBeNull();
    });

    it("saves the user's pick when it differs from GAIA's", () => {
        const p = P();
        expect(savedVision(p.catalog!, pickVision(initialSelection(p), p.catalog!, GEMMA.id))).toBe(GEMMA.id);
        expect(savedVision(p.catalog!, pickVision(initialSelection(p), p.catalog!, OTHER_VISION.id))).toBe(OTHER_VISION.id);
    });

    it('saves nothing for Skip for now', () => {
        const p = P();
        const sel = pickVision(pickChat(initialSelection(p), p.catalog!, GPT_OSS.id), p.catalog!, VISION_NONE);
        expect(savedVision(p.catalog!, sel)).toBeNull();
    });
});
