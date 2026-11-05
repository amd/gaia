// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The model new turns run on, and where it runs (this PC or a cloud provider). */

import { forgetModelChecks } from './modelNeededStore';
import { create } from 'zustand';
import * as api from '../services/api';
import { useChatStore } from './chatStore';
import { log } from '../utils/logger';
import type { ActiveModel, Session, WarmUpStatus } from '../types';

/** A local model picked from the list, loading in the background; `error` once that load failed. */
export interface PickLoad {
    model: string;
    seconds: number;
    error: string | null;
}

interface ModelState {
    active: ActiveModel | null;
    error: string | null;
    /** One-line notice about restoring the last model, shown once per launch. */
    restoreNotice: { ok: boolean; message: string } | null;
    /** A model picked while a reply was running; applied when that reply ends. */
    queued: string | null;
    /** Why applying the queued model failed, shown on the chip. */
    queueError: string | null;
    /** Friendly names the backend gave, by model id. */
    names: Record<string, string>;
    pickLoad: PickLoad | null;
    refresh: () => Promise<void>;
    retryNow: () => void;
    select: (model: string) => Promise<void>;
    queue: (model: string | null) => void;
    applyQueued: () => Promise<void>;
    rememberNames: (models: Array<{ id: string; display_name?: string }>) => void;
    dismissRestoreNotice: () => void;
    /** Ask for the failed pick's load again. */
    retryPickLoad: () => Promise<void>;
    dismissPickLoad: () => void;
}

export const PICK_LOAD_POLL_MS = 1_000;
const RESTORE_RETRY_MS = 5_000;
const RESTORE_RETRIES = 24;
/** A failed read is retried, doubling up to the cap: the backend may still be starting. */
export const MODEL_RETRY_INITIAL_MS = 1_000;
export const MODEL_RETRY_MAX_MS = 30_000;
let restoreRetries = 0;
let namesRequested = false;
let failRetryTimer: ReturnType<typeof setTimeout> | null = null;
let failRetryMs = MODEL_RETRY_INITIAL_MS;
// Bumped by each pick, so an older pick's watch stops.
let pickLoadWatch = 0;

function clearFailRetry(): void {
    if (failRetryTimer) clearTimeout(failRetryTimer);
    failRetryTimer = null;
}

/** Forget pending retries and backoff, so each test starts from a fresh launch. */
export function resetModelRetryForTests(): void {
    clearFailRetry();
    failRetryMs = MODEL_RETRY_INITIAL_MS;
    restoreRetries = 0;
    namesRequested = false;
    pickLoadWatch += 1;
}

/** The pick's own warm-up: one on another model is still finishing, and the pick's runs next. */
function isPickWarmUp(w: WarmUpStatus, model: string): boolean {
    return !w.model || sameModelId(w.model, model);
}

/** Follow the backend's load of a picked local model until it is ready or has failed. */
function watchPickLoad(model: string, set: (s: Partial<ModelState>) => void): void {
    const watch = ++pickLoadWatch;
    set({ pickLoad: { model, seconds: 0, error: null } });
    const poll = async () => {
        const w: WarmUpStatus = await api.getWarmUp();
        if (watch !== pickLoadWatch) return;
        const seconds = Math.floor(w.elapsed_seconds ?? 0);
        if (w.state === 'warming') {
            set({ pickLoad: { model, seconds: isPickWarmUp(w, model) ? seconds : 0, error: null } });
            setTimeout(() => { void poll().catch(fail); }, PICK_LOAD_POLL_MS);
        } else if (w.state === 'failed' && isPickWarmUp(w, model)) {
            set({ pickLoad: { model, seconds, error: w.error || 'The load stopped without saying why.' } });
        } else {
            set({ pickLoad: null });
        }
    };
    // A watch that breaks says so rather than leave "Loading" up for good.
    const fail = (err: unknown) => {
        if (watch !== pickLoadWatch) return;
        log.system.warn(`Lost track of loading ${model}`, err);
        const reason = err instanceof Error ? err.message : String(err);
        set({ pickLoad: { model, seconds: 0, error: `GAIA couldn't check on the load: ${reason}` } });
    };
    void poll().catch(fail);
}

/** Local models' friendly names, read once so every surface names a model as the chip does. */
async function loadLocalNames(remember: ModelState['rememberNames']): Promise<void> {
    try {
        const r = await api.listProviderModels('local');
        if (r?.models) remember(r.models);
    } catch (err) {
        namesRequested = false;
        log.system.warn('Model names unavailable; raw model ids show until the model list loads', err);
    }
}

export const useModelStore = create<ModelState>((set, get) => ({
    active: null,
    error: null,
    restoreNotice: null,
    queued: null,
    queueError: null,
    names: {},
    pickLoad: null,
    refresh: async () => {
        if (!namesRequested) {
            namesRequested = true;
            void loadLocalNames(get().rememberNames);
        }
        try {
            const active = await api.getActiveModel();
            clearFailRetry();
            failRetryMs = MODEL_RETRY_INITIAL_MS;
            const r = active.restore;
            // The model server is still starting; the saved model is checked once it answers.
            if (r?.status === 'pending' && restoreRetries < RESTORE_RETRIES) {
                restoreRetries += 1;
                setTimeout(() => { void get().refresh(); }, RESTORE_RETRY_MS);
            }
            set({
                active,
                error: null,
                ...(r && r.status !== 'none' && r.message
                    ? { restoreNotice: { ok: r.status === 'restored', message: r.message } }
                    : {}),
            });
        } catch (err) {
            set({ error: err instanceof Error ? err.message : String(err) });
            // Without this the chip says "Model" for the whole session when the
            // window loaded before the backend listened.
            if (!failRetryTimer) {
                failRetryTimer = setTimeout(() => { failRetryTimer = null; void get().refresh(); }, failRetryMs);
                failRetryMs = Math.min(failRetryMs * 2, MODEL_RETRY_MAX_MS);
            }
        }
    },
    /** Read the model now if the last read failed (the backend just became reachable). */
    retryNow: () => {
        if (!failRetryTimer) return;
        clearFailRetry();
        void get().refresh();
    },
    select: async (model) => {
        forgetModelChecks();
        const active = await api.selectModel(model);
        // Sessions name the model they run; refetch so the chip doesn't lag a poll behind.
        let sessions: Session[] | null = null;
        try {
            sessions = (await api.listSessions()).sessions;
        } catch (err) {
            log.system.error('Could not refresh chats after a model switch; the next poll will', err);
        }
        set({ active, error: null, restoreNotice: null });
        // The backend starts loading a local pick; follow it so the wait is visible.
        if (active.remote) {
            pickLoadWatch += 1;
            set({ pickLoad: null });
        } else {
            watchPickLoad(model, set);
        }
        // An empty list has no model to report, and must not wipe the sidebar.
        if (sessions?.length) useChatStore.getState().setSessions(sessions);
    },
    queue: (model) => set({ queued: model, queueError: null }),
    applyQueued: async () => {
        const model = get().queued;
        if (!model) return;
        try {
            await get().select(model);
            set({ queued: null });
        } catch (err) {
            set({
                queued: null,
                queueError: `Couldn't switch to ${displayModelName(model)}: ${err instanceof Error ? err.message : String(err)}`,
            });
        }
    },
    rememberNames: (models) => {
        const next = { ...get().names };
        for (const m of models) if (m.display_name) next[m.id] = m.display_name;
        set({ names: next });
    },
    dismissRestoreNotice: () => set({ restoreNotice: null }),
    retryPickLoad: async () => {
        const model = get().pickLoad?.model;
        if (!model) return;
        try {
            await api.requestWarmUp();
        } catch (err) {
            const reason = err instanceof Error ? err.message : String(err);
            set({ pickLoad: { model, seconds: 0, error: `GAIA couldn't start the load: ${reason}` } });
            return;
        }
        watchPickLoad(model, set);
    },
    dismissPickLoad: () => {
        pickLoadWatch += 1;
        set({ pickLoad: null });
    },
}));

// A pick made during a reply keeps that reply on its model, then applies.
useChatStore.subscribe((state, prev) => {
    if (prev.isStreaming && !state.isStreaming && useModelStore.getState().queued) {
        void useModelStore.getState().applyQueued();
    }
});

/** Re-render when friendly names arrive; pair with displayModelName in render. */
export function useModelNames(): Record<string, string> {
    return useModelStore((s) => s.names);
}

/** Same local model: Lemonade lists a registered `user.X` as `X`, in any case. */
export function sameModelId(a: string | null | undefined, b: string | null | undefined): boolean {
    if (!a || !b) return false;
    const bare = (id: string) => id.toLowerCase().replace(/^user\./, '');
    return bare(a) === bare(b);
}

/** The friendly name the backend gave a model, else its short id. */
export function displayModelName(model: string): string {
    const state = useModelStore.getState();
    if (state.names[model]) return state.names[model];
    if (state.active?.model === model && state.active.display_name) return state.active.display_name;
    return shortModelName(model);
}

/** The backend's answer to where a session's chat is answered (`inference_remote: null` = unknown). */
export type InferenceLocation = Pick<
    Session,
    'inference_remote' | 'inference_provider_name' | 'inference_description' | 'effective_model'
>;

/**
 * The open session's location, else the most recent one's (the model choice is
 * global, so it answers for every session). Undefined when there is no session.
 */
export function selectInferenceLocation(
    sessions: Session[],
    currentSessionId: string | null,
): InferenceLocation | undefined {
    return sessions.find((s) => s.id === currentSessionId) ?? sessions[0];
}

/** Where new turns are answered, for the place-of-inference indicators. */
export interface InferencePlace {
    remote: boolean;
    /** "Local" or the cloud provider's name. */
    label: string;
    /** The backend's full sentence, when it applies to the picked model. */
    description: string | null;
}

/**
 * Claims "Local" only when the backend says the chat stays on this PC. A cloud
 * pick counts at once, before the polled session list catches up. Null when
 * neither can tell, so the UI makes no claim.
 */
export function inferencePlace(
    active: ActiveModel | null,
    location: InferenceLocation | undefined,
): InferencePlace | null {
    // The chat names a model this window's pick doesn't (another window or the TUI
    // changed it): its own location goes with that name, or the chip contradicts itself.
    const pickIsStale =
        !!location?.effective_model && !!active?.model && location.effective_model !== active.model;
    if (active?.remote && !(pickIsStale && location?.inference_remote !== undefined)) {
        return {
            remote: true,
            label: active.provider === 'fireworks' ? 'Fireworks AI' : 'AMD LLM Gateway',
            description: null,
        };
    }
    if (location?.inference_remote === true) {
        return {
            remote: true,
            label: location.inference_provider_name || 'a cloud provider',
            description: location.inference_description ?? null,
        };
    }
    if (location?.inference_remote === false) {
        return { remote: false, label: 'Local', description: location.inference_description ?? null };
    }
    // No chat yet: the backend's answer for where a new chat would run.
    if (location === undefined && active?.inference_remote === false) {
        return { remote: false, label: 'Local', description: active.inference_description ?? null };
    }
    return null;
}

export const UNKNOWN_PLACE_TITLE =
    "GAIA can't tell whether this chat is answered on this PC or by a cloud provider, so it isn't claiming either.";

/** Place of inference for the open chat, from the backend's session location and the picked model. */
export function useInferencePlace(): InferencePlace | null {
    const active = useModelStore((s) => s.active);
    const location = useChatStore((s) => selectInferenceLocation(s.sessions, s.currentSessionId));
    return inferencePlace(active, location);
}

/**
 * The model the open chat's next turn runs, as the backend resolved it for that
 * session. With no chat open, the picked model, which a new chat starts on.
 */
export function selectChipModel(
    active: ActiveModel | null,
    sessions: Session[],
    currentSessionId: string | null,
): string | null {
    const session = sessions.find((s) => s.id === currentSessionId);
    return session?.effective_model || active?.model || null;
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
export function distinctModelLabels(models: Array<{ id: string; display_name?: string | null }>): Record<string, string> {
    const labels: Record<string, string> = {};
    for (const m of models) labels[m.id] = m.display_name || displayModelName(m.id);
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

/** A cloud provider's name as people know it; an unknown id is shown as given. */
export function cloudProviderName(provider: string): string {
    return provider === 'fireworks' ? 'Fireworks AI' : provider === 'amd' ? 'AMD LLM Gateway' : provider;
}
