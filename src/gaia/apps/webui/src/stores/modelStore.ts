// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The model new turns run on, and where it runs (this PC or a cloud provider). */

import { create } from 'zustand';
import * as api from '../services/api';
import { useChatStore } from './chatStore';
import { log } from '../utils/logger';
import type { ActiveModel, Session } from '../types';

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
    refresh: () => Promise<void>;
    select: (model: string) => Promise<void>;
    queue: (model: string | null) => void;
    applyQueued: () => Promise<void>;
    rememberNames: (models: Array<{ id: string; display_name?: string }>) => void;
    dismissRestoreNotice: () => void;
}

const RESTORE_RETRY_MS = 5_000;
const RESTORE_RETRIES = 24;
let restoreRetries = 0;

export const useModelStore = create<ModelState>((set, get) => ({
    active: null,
    error: null,
    restoreNotice: null,
    queued: null,
    queueError: null,
    names: {},
    refresh: async () => {
        try {
            const active = await api.getActiveModel();
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
        }
    },
    select: async (model) => {
        const active = await api.selectModel(model);
        // Sessions name the model they run; refetch so the chip doesn't lag a poll behind.
        let sessions: Session[] | null = null;
        try {
            sessions = (await api.listSessions()).sessions;
        } catch (err) {
            log.system.error('Could not refresh chats after a model switch; the next poll will', err);
        }
        set({ active, error: null, restoreNotice: null });
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
}));

// A pick made during a reply keeps that reply on its model, then applies.
useChatStore.subscribe((state, prev) => {
    if (prev.isStreaming && !state.isStreaming && useModelStore.getState().queued) {
        void useModelStore.getState().applyQueued();
    }
});

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
    'inference_remote' | 'inference_provider_name' | 'inference_description'
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
    if (active?.remote) {
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

/** The model name without its provider prefix or catalogue path. */
export function shortModelName(model: string): string {
    const withoutProvider = /^(fireworks|amd)\./.test(model) ? model.slice(model.indexOf('.') + 1) : model;
    return withoutProvider.slice(withoutProvider.lastIndexOf('/') + 1);
}
