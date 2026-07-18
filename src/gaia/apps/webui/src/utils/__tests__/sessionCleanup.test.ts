// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { isAbandonedDraft } from '../sessionCleanup';
import type { Session } from '../../types';

function session(overrides: Partial<Session> = {}): Session {
    return {
        id: 's1',
        title: 'New Task',
        created_at: '2026-07-10T10:00:00Z',
        updated_at: '2026-07-10T10:00:00Z',
        model: 'm',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        ...overrides,
    };
}

const baseState = {
    sessions: [session()],
    currentSessionId: 's1',
    messages: [] as unknown[],
    isStreaming: false,
    runningSessionIds: [] as string[],
};

describe('isAbandonedDraft', () => {
    it('is true for an untouched current "New Task" draft', () => {
        expect(isAbandonedDraft('s1', baseState)).toBe(true);
    });

    it('is false when the session is unknown', () => {
        expect(isAbandonedDraft('missing', baseState)).toBe(false);
    });

    it('is false once the title was changed (renamed / auto-titled)', () => {
        const state = { ...baseState, sessions: [session({ title: 'Summarize my PDF' })] };
        expect(isAbandonedDraft('s1', state)).toBe(false);
    });

    it('is false when the backend reports messages', () => {
        const state = { ...baseState, sessions: [session({ message_count: 2 })] };
        expect(isAbandonedDraft('s1', state)).toBe(false);
    });

    it('is false when the current session has loaded messages', () => {
        const state = { ...baseState, messages: [{}, {}] };
        expect(isAbandonedDraft('s1', state)).toBe(false);
    });

    it('is false while the current session is streaming', () => {
        const state = { ...baseState, isStreaming: true };
        expect(isAbandonedDraft('s1', state)).toBe(false);
    });

    it('is false when a turn is running server-side', () => {
        const state = { ...baseState, runningSessionIds: ['s1'] };
        expect(isAbandonedDraft('s1', state)).toBe(false);
    });

    it('ignores the loaded-messages signal for a non-current session', () => {
        // messages reflect the CURRENT session only; a background empty draft
        // should still be reapable even though `messages` is populated here.
        const state = {
            ...baseState,
            currentSessionId: 'other',
            messages: [{}, {}],
        };
        expect(isAbandonedDraft('s1', state)).toBe(true);
    });
});
