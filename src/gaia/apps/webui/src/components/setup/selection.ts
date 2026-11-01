// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Which models the user ticked, and what that costs: the rules the setup
 * checklist and Settings > Models share. Pure functions over the backend's
 * plan, so the numbers shown are the backend's numbers added up, never guesses.
 */

import type { GpuMemoryStatus, SetupCatalogEntry, SetupPlan } from '../../types';

export interface Selection {
    /** Models the user wants on this PC. */
    ticked: ReadonlySet<string>;
    /** The chat model new chats use; null when none is ticked or installed. */
    defaultChat: string | null;
    /** Models the user unticked by hand (not ones never ticked). */
    unticked: ReadonlySet<string>;
}

export const catalogOf = (plan: SetupPlan | null): SetupCatalogEntry[] => plan?.catalog ?? [];

export const isVision = (e: SetupCatalogEntry) => e.roles.includes('vision');
export const isEmbedding = (e: SetupCatalogEntry) => e.roles.includes('embedding');
export const isNpu = (e: SetupCatalogEntry) => e.recipe === 'flm';

/** GAIA's models: what setup plans, what fits this PC, what is on disk. Shown first. */
export function mainRows(catalog: SetupCatalogEntry[]): SetupCatalogEntry[] {
    return catalog.filter((e) => e.gaia_model && (e.planned || e.recommended || e.downloaded || e.partial));
}

/** GAIA's other models the user can tick: shown behind a disclosure. */
export function moreRows(catalog: SetupCatalogEntry[]): SetupCatalogEntry[] {
    const main = new Set(mainRows(catalog).map((e) => e.id));
    return catalog.filter((e) => e.gaia_model && !main.has(e.id));
}

/** Chat models already in Lemonade that GAIA didn't plan (FLM, NPU, the user's own). */
export function installedOnlyRows(catalog: SetupCatalogEntry[]): SetupCatalogEntry[] {
    return catalog.filter((e) => !e.gaia_model && e.downloaded);
}

/** What setup downloads now, plus GAIA's models already on disk. */
export function initialSelection(plan: SetupPlan): Selection {
    const catalog = catalogOf(plan);
    const ticked = new Set(
        catalog.filter((e) => e.selected || (e.gaia_model && e.downloaded && !e.skipped)).map((e) => e.id),
    );
    return { ticked, defaultChat: plan.chat_model.id, unticked: new Set() };
}

/** The chat models the user can make the default: ticked, or already on this PC. */
export function chatCandidates(catalog: SetupCatalogEntry[], ticked: ReadonlySet<string>): SetupCatalogEntry[] {
    return catalog.filter((e) => e.chat_choice && (ticked.has(e.id) || e.downloaded));
}

/** Tick or untick one model, keeping the default chat model one the user can still use. */
export function toggle(sel: Selection, catalog: SetupCatalogEntry[], id: string, on: boolean): Selection {
    const ticked = new Set(sel.ticked);
    const unticked = new Set(sel.unticked);
    if (on) {
        ticked.add(id);
        unticked.delete(id);
    } else {
        ticked.delete(id);
        unticked.add(id);
    }
    const candidates = chatCandidates(catalog, ticked);
    let defaultChat = sel.defaultChat;
    if (!defaultChat || !candidates.some((e) => e.id === defaultChat)) {
        const entry = catalog.find((e) => e.id === id);
        defaultChat = on && entry?.chat_choice ? id
            : (candidates.find((e) => e.recommended) ?? candidates[0])?.id ?? null;
    }
    return { ticked, defaultChat, unticked };
}

/** Back to the recommended set for this PC. */
export function recommendedSelection(plan: SetupPlan): Selection {
    const catalog = catalogOf(plan);
    const ticked = new Set(catalog.filter((e) => e.recommended || (e.gaia_model && e.downloaded)).map((e) => e.id));
    const candidates = chatCandidates(catalog, ticked);
    const pick = plan.recommended_model && candidates.some((e) => e.id === plan.recommended_model)
        ? plan.recommended_model
        : (candidates.find((e) => e.recommended) ?? candidates[0])?.id ?? null;
    return { ticked, defaultChat: pick, unticked: new Set() };
}

/** Models the selection still has to download, largest first. */
export function pendingDownloads(catalog: SetupCatalogEntry[], sel: Selection): SetupCatalogEntry[] {
    return catalog
        .filter((e) => sel.ticked.has(e.id) && !e.downloaded)
        .sort((a, b) => (b.size_gb ?? 0) - (a.size_gb ?? 0));
}

export interface Totals {
    /** GB to download; null when a ticked model's size is unknown. */
    downloadGb: number | null;
    freeGb: number | null;
    /** Free space after the download; null when either side is unknown. */
    afterGb: number | null;
}

export function totals(plan: SetupPlan, sel: Selection): Totals {
    const pending = pendingDownloads(catalogOf(plan), sel);
    const sizes = pending.map((e) => e.size_gb);
    const downloadGb = sizes.every((s): s is number => s != null) ? round1(sizes.reduce((a, b) => a + b, 0)) : null;
    const freeGb = plan.disk?.free_gb ?? null;
    return { downloadGb, freeGb, afterGb: downloadGb != null && freeGb != null ? round1(freeGb - downloadGb) : null };
}

export interface Shortfall {
    needGb: number;
    freeGb: number;
    /** How much to free for the whole selection to fit. */
    freeUpGb: number;
    /** The largest pending model whose skip alone makes it fit, else null. */
    drop: SetupCatalogEntry | null;
}

/** The disk shortfall of the selection, as the backend's `disk_shortfall` words it; null when it fits or is unknown. */
export function shortfall(plan: SetupPlan, sel: Selection): Shortfall | null {
    const { downloadGb, freeGb } = totals(plan, sel);
    if (downloadGb == null || freeGb == null || downloadGb <= freeGb) return null;
    const freeUpGb = round1(downloadGb - freeGb);
    const drop = pendingDownloads(catalogOf(plan), sel).find((e) => e.size_gb != null && downloadGb - e.size_gb <= freeGb) ?? null;
    return { needGb: downloadGb, freeGb, freeUpGb, drop };
}

/** Ticked models (and the default chat model) that may not fit this PC's memory. */
export function tooBig(catalog: SetupCatalogEntry[], sel: Selection): SetupCatalogEntry[] {
    return catalog.filter((e) => e.fits === false && (sel.ticked.has(e.id) || e.id === sel.defaultChat));
}

/** Vision is lost when the vision model is neither ticked nor on disk. */
export function visionSkipped(catalog: SetupCatalogEntry[], sel: Selection): SetupCatalogEntry | null {
    return catalog.find((e) => isVision(e) && e.gaia_model && !sel.ticked.has(e.id)) ?? null;
}

/** The skip-list changes that turn `plan` (after any chat-model change) into the selection. */
export function skipChanges(plan: SetupPlan, sel: Selection): Array<{ id: string; skipped: boolean }> {
    const changes: Array<{ id: string; skipped: boolean }> = [];
    for (const e of catalogOf(plan)) {
        if (!e.planned) continue;
        // The chat model in use is never skipped: setup would then have none.
        if (e.id === plan.chat_model.id) {
            if (e.skipped) changes.push({ id: e.id, skipped: false });
            continue;
        }
        const want = sel.ticked.has(e.id);
        if (!want && !e.skipped) changes.push({ id: e.id, skipped: true });
        if (want && e.skipped) changes.push({ id: e.id, skipped: false });
    }
    return changes;
}

/** Ticked models setup doesn't plan and that aren't on disk: pulled beside the plan's. */
export function extraModels(plan: SetupPlan, sel: Selection): string[] {
    return catalogOf(plan).filter((e) => sel.ticked.has(e.id) && !e.planned && !e.downloaded).map((e) => e.id);
}

/** Models a bigger GPU share would let run. */
export function gpuUnlocks(gpu: GpuMemoryStatus | null): string[] {
    return [...new Set((gpu?.presets ?? []).flatMap((p) => p.unlocks))];
}

/**
 * The GPU step shows on an integrated GPU where a bigger share makes a default
 * model fit, unless the user unticked every model it would help.
 */
export function showGpuStep(gpu: GpuMemoryStatus | null, sel: Selection): boolean {
    if (!gpu?.applies || !gpu.show_in_setup) return false;
    return gpuUnlocks(gpu).some((id) => !sel.unticked.has(id));
}

function round1(n: number): number {
    return Math.round(n * 10) / 10;
}
