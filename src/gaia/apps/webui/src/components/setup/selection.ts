// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Which models the user ticked, and what that costs: the rules the setup
 * checklist and Settings > Models share. Pure functions over the backend's
 * plan, so the numbers shown are the backend's numbers added up, never guesses.
 */

import type { SetupCatalogEntry, SetupOverall, SetupPlan, SetupRunStatus } from '../../types';

export interface Selection {
    /** Models the user wants on this PC. */
    ticked: ReadonlySet<string>;
    /** The chat model new chats use; null when none is ticked or installed. */
    defaultChat: string | null;
    /** Models the user unticked by hand (not ones never ticked). */
    unticked: ReadonlySet<string>;
    /** The user picked the chat model themselves, now or in an earlier setup. */
    chatChosen: boolean;
    /** The model that reads images; null is GAIA's choice, `VISION_NONE` none for now. */
    vision: string | null;
}

export const VISION_NONE = 'none';

export const catalogOf = (plan: SetupPlan | null): SetupCatalogEntry[] => plan?.catalog ?? [];

export const isVision = (e: SetupCatalogEntry) => e.roles.includes('vision');
export const isEmbedding = (e: SetupCatalogEntry) => e.roles.includes('embedding');
export const isNpu = (e: SetupCatalogEntry) => e.recipe === 'flm';
export const isOptional = (e: SetupCatalogEntry) => e.support === null;
export const canCreateImages = (chat: SetupCatalogEntry | undefined) => chat?.feature_tier !== 'compact';

/** The model that reads images for this selection: the user's, else the chat model
 *  when it can, else GAIA's vision model. Null when the user skipped it. */
export function resolveVision(catalog: SetupCatalogEntry[], sel: Selection): string | null {
    if (sel.vision === VISION_NONE) return null;
    if (sel.vision) return sel.vision;
    const chat = catalog.find((e) => e.id === sel.defaultChat);
    if (chat?.reads_images) return chat.id;
    return catalog.find((e) => isVision(e) && e.gaia_model)?.id ?? null;
}

/** Ticks follow the vision pick, so a model nothing uses is never downloaded. */
function applyVision(sel: Selection, catalog: SetupCatalogEntry[]): Selection {
    const pick = resolveVision(catalog, sel);
    const ticked = new Set(sel.ticked);
    const unticked = new Set(sel.unticked);
    for (const e of catalog) {
        if (!isVision(e) || e.id === sel.defaultChat) continue;
        if (e.id === pick) {
            ticked.add(e.id);
            unticked.delete(e.id);
        } else if (!e.downloaded) {
            ticked.delete(e.id);
            unticked.add(e.id);
        }
    }
    return { ...sel, ticked, unticked };
}

/** Pick the chat model: the one it replaces stops downloading, and the jobs that
 *  depend on it follow. */
export function pickChat(sel: Selection, catalog: SetupCatalogEntry[], id: string): Selection {
    const ticked = new Set(sel.ticked);
    const previous = catalog.find((e) => e.id === sel.defaultChat);
    if (previous && !previous.downloaded) ticked.delete(previous.id);
    const entry = catalog.find((e) => e.id === id);
    if (!entry?.downloaded) ticked.add(id);
    if (!canCreateImages(entry)) {
        for (const e of catalog) if (e.roles.includes('image_creation')) ticked.delete(e.id);
    }
    return applyVision({ ...sel, ticked, defaultChat: id, chatChosen: true, vision: null }, catalog);
}

/** Pick the model that reads images; null goes back to GAIA's choice. */
export function pickVision(sel: Selection, catalog: SetupCatalogEntry[], id: string | null): Selection {
    return applyVision({ ...sel, vision: id }, catalog);
}

/** What to save as `vision_model`: nothing when the chat model does the job. */
export function savedVision(catalog: SetupCatalogEntry[], sel: Selection): string | null {
    if (sel.vision === VISION_NONE) return null;
    const pick = resolveVision(catalog, sel);
    const chat = catalog.find((e) => e.id === sel.defaultChat);
    const gaiaChoice = chat?.reads_images ? chat.id : catalog.find((e) => isVision(e) && e.gaia_model)?.id;
    return pick === gaiaChoice ? null : pick;
}

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
    const chat = catalog.find((e) => e.id === plan.chat_model.id);
    const visionSkipped = !chat?.reads_images && catalog.some((e) => isVision(e) && e.gaia_model && e.skipped);
    return applyVision(
        { ticked, defaultChat: plan.chat_model.id, unticked: new Set(), chatChosen: plan.chat_model.user_set, vision: plan.vision_model ?? (visionSkipped ? VISION_NONE : null) },
        catalog,
    );
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
    return { ...sel, ticked, defaultChat, unticked };
}

/** Make *id* the default chat model by the user's choice, ticking it if it isn't on disk. */
export function chooseChat(sel: Selection, catalog: SetupCatalogEntry[], id: string): Selection {
    const onDisk = catalog.find((e) => e.id === id)?.downloaded;
    return { ...sel, defaultChat: id, chatChosen: true, ticked: onDisk ? sel.ticked : new Set([...sel.ticked, id]) };
}

/** Back to the recommended set for this PC. */
export function recommendedSelection(plan: SetupPlan): Selection {
    const catalog = catalogOf(plan);
    const ticked = new Set(catalog.filter((e) => e.recommended || (e.gaia_model && e.downloaded)).map((e) => e.id));
    const candidates = chatCandidates(catalog, ticked);
    const pick = plan.recommended_model && candidates.some((e) => e.id === plan.recommended_model)
        ? plan.recommended_model
        : (candidates.find((e) => e.recommended) ?? candidates[0])?.id ?? null;
    return applyVision({ ticked, defaultChat: pick, unticked: new Set(), chatChosen: false, vision: null }, catalog);
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
        if (!e.planned) {
            // A model removed earlier is on the skip list, which drops it from
            // every download: ticking it again takes it off.
            if (e.skipped && sel.ticked.has(e.id)) changes.push({ id: e.id, skipped: false });
            continue;
        }
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

/**
 * The download's overall progress from the same plan the models step totalled:
 * only what was left to download when it started, so the two screens never
 * disagree (a model already on disk adds nothing; a partial one only its rest).
 */
export function overallFromPlan(status: SetupRunStatus, plan: SetupPlan | null): SetupOverall | undefined {
    const rows = (status.models ?? []).filter((m) => m.state !== 'skipped');
    if (!plan?.catalog || rows.length === 0) return status.overall;
    let done = 0;
    let total = 0;
    for (const m of rows) {
        const entry = plan.catalog.find((e) => e.id === m.id);
        const left = entry?.downloaded ? 0
            : entry?.size_gb != null ? Math.min(m.bytes_total || Infinity, entry.size_gb * 1e9)
                : m.bytes_total;
        const before = Math.max(0, m.bytes_total - left);
        total += left;
        done += m.state === 'ready' || m.state === 'verifying' ? left : Math.min(left, Math.max(0, m.bytes_done - before));
    }
    return {
        bytes_done: done,
        bytes_total: total,
        speed_bps: status.overall?.speed_bps ?? 0,
        eta_s: null,
        percent: total > 0 ? Math.floor((done * 100) / total) : 100,
        failed: rows.filter((m) => m.state === 'failed').length,
    };
}

function round1(n: number): number {
    return Math.round(n * 10) / 10;
}
