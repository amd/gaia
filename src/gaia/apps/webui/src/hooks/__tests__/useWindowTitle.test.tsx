// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { useWindowTitle } from '../useWindowTitle';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';

function Harness() {
    useWindowTitle();
    return null;
}

function chat(id: string, title: string): Session {
    return {
        id, title, created_at: '', updated_at: '', model: '', system_prompt: null,
        message_count: 0, document_ids: [], kind: 'chat',
    };
}

describe('window title', () => {
    beforeEach(() => {
        document.title = 'GAIA Agent UI';
        useChatStore.setState({ sessions: [chat('a', 'Name three birds'), chat('b', 'Trip to Lisbon')], currentSessionId: null });
    });

    it('names the open chat, and follows a switch, a rename and a new chat', () => {
        render(<Harness />);
        expect(document.title).toBe('GAIA Agent UI');

        act(() => useChatStore.setState({ currentSessionId: 'a' }));
        expect(document.title).toBe('Name three birds - GAIA');

        act(() => useChatStore.setState({ currentSessionId: 'b' }));
        expect(document.title).toBe('Trip to Lisbon - GAIA');

        act(() => useChatStore.setState({ sessions: [chat('a', 'Name three birds'), chat('b', 'Lisbon in May')] }));
        expect(document.title).toBe('Lisbon in May - GAIA');

        act(() => useChatStore.setState({ currentSessionId: null }));
        expect(document.title).toBe('GAIA Agent UI');
    });
});
