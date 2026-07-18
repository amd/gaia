// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Abandoned-draft session cleanup (#2119, phantom tasks).
 *
 * Clicking "Start a New Task" persists a session immediately, before any
 * message is sent. Abandoning that click used to leave a permanent "New Task"
 * row in the sidebar — 40+ of them piled up in the live E2E round. This helper
 * deletes such a session when the user navigates away from it, so abandoned
 * clicks stop compounding into list debris.
 *
 * A session qualifies as an abandoned draft ONLY when every emptiness signal
 * agrees, so a session that has actually started a turn is never deleted:
 *   - it still has the default "New Task" title (never renamed/auto-titled),
 *   - the backend reports zero messages,
 *   - it has no in-flight turn (not streaming here, not in the running set),
 *   - and, when it is the current session, the loaded message list is empty.
 */

import type { Session } from '../types';
import { useChatStore } from '../stores/chatStore';
import * as api from '../services/api';
import { log } from './logger';

const DRAFT_TITLE = 'New Task';

interface DraftCheckState {
    sessions: Session[];
    currentSessionId: string | null;
    messages: unknown[];
    isStreaming: boolean;
    runningSessionIds: string[];
}

/**
 * Pure predicate (state passed in) so it can be unit-tested without the store.
 * Returns true when ``sessionId`` is an abandoned "New Task" draft safe to drop.
 */
export function isAbandonedDraft(sessionId: string, state: DraftCheckState): boolean {
    const s = state.sessions.find((x) => x.id === sessionId);
    if (!s) return false;
    if (s.title !== DRAFT_TITLE) return false;
    if ((s.message_count ?? 0) !== 0) return false;
    if (state.runningSessionIds.includes(sessionId)) return false;
    // `messages` only reflects the CURRENT session, so it's only authoritative
    // for the current one. A background session relies on message_count above.
    if (state.currentSessionId === sessionId) {
        if (state.messages.length !== 0) return false;
        if (state.isStreaming) return false;
    }
    return true;
}

/**
 * Delete an abandoned-draft session (backend + store) if it qualifies.
 * No-op when ``sessionId`` is null or the session has any real content/activity.
 * Resolves to true when a delete was performed.
 */
export async function cleanupAbandonedDraft(sessionId: string | null): Promise<boolean> {
    if (!sessionId) return false;
    const store = useChatStore.getState();
    const state: DraftCheckState = {
        sessions: store.sessions,
        currentSessionId: store.currentSessionId,
        messages: store.messages,
        isStreaming: store.isStreaming,
        runningSessionIds: store.runningSessionIds,
    };
    if (!isAbandonedDraft(sessionId, state)) return false;

    log.chat.info(`Removing abandoned draft session ${sessionId}`);
    // Mark pending-delete first so the 5s session poll can't resurrect it.
    store.addPendingDelete(sessionId);
    store.removeSession(sessionId);
    try {
        await api.deleteSession(sessionId);
        return true;
    } catch (err) {
        log.chat.warn(`Failed to delete abandoned draft ${sessionId}`, err);
        return false;
    } finally {
        store.removePendingDelete(sessionId);
    }
}
