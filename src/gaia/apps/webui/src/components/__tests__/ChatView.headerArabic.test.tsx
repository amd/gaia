// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** In Arabic the chat header's buttons and the sidebar's resize handle used to keep English names and hover text. */

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { readsAs } from '../../test/readsAs';
import type { Session } from '../../types';

const session: Session = {
    id: 'arabic-header', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown = {};
        if (url.pathname === `/api/sessions/${session.id}/messages`) body = { messages: [], total: 0 };
        else if (url.pathname.startsWith('/api/chat/permissions/')) body = { session_id: session.id, mode: 'ask', grants: [] };
        else if (url.pathname === '/api/chat/active') body = { session_ids: [] };
        else if (url.pathname === '/api/documents') body = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };
        return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }));
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
    act(() => useChatStore.getState().setLocale('ar'));
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('Arabic chat header', () => {
    it('names the private, browse and export buttons in Arabic', () => {
        render(<ChatView sessionId={session.id} />);
        const private_ = screen.getByRole('button', { name: readsAs('تفعيل المحادثة الخاصة (Turn on private chat)') });
        expect(private_.getAttribute('title')).toContain('لن تُحفظ في الذاكرة');
        const browse = screen.getByRole('button', { name: readsAs('تصفح الملفات على هذا الجهاز (Browse files on this PC)') });
        expect(browse.getAttribute('title')).toContain('تصفح الملفات على هذا الجهاز');
        const exportBtn = screen.getByRole('button', { name: readsAs('تصدير المحادثة (Export chat)') });
        expect(exportBtn.getAttribute('title')).toContain('تصدير بصيغة Markdown');
    });
});

describe('Arabic sidebar resize handle', () => {
    it('names the handle and its hover text in Arabic', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        const handle = screen.getByRole('separator', { name: readsAs('تغيير حجم الشريط الجانبي (Resize sidebar)') });
        expect(handle.getAttribute('title')).toContain('اسحب لتغيير الحجم');
    });
});
