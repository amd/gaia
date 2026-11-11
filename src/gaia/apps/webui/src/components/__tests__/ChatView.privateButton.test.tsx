// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The Private button used to look the same on and off, so turning it on seemed to do nothing. */

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
import chatViewCss from '../ChatView.css?raw';

const base: Session = {
    id: 'private-me', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

function renderChat(isPrivate: boolean) {
    const session = { ...base, private: isPrivate };
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
    render(<ChatView sessionId={session.id} />);
}

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true, value: vi.fn(),
    });
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === `/api/sessions/${base.id}/messages`) {
            body = { messages: [], total: 0 };
        } else if (url.pathname.startsWith('/api/chat/permissions/')) {
            body = { session_id: base.id, mode: 'ask', grants: [] };
        } else if (url.pathname === '/api/chat/active') {
            body = { session_ids: [] };
        } else if (url.pathname === '/api/documents') {
            body = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };
        } else {
            throw new Error(`Unexpected request: ${url}`);
        }
        return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }));
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

function ruleBody(selector: string): string | undefined {
    const at = chatViewCss.indexOf(`${selector} {`);
    if (at < 0) return undefined;
    return chatViewCss.slice(at, chatViewCss.indexOf('}', at));
}

describe('the header Private button says what it will do', () => {
    it('offers to turn private off, crossed-out eye and filled, when the chat is private', () => {
        renderChat(true);
        const button = screen.getByRole('button', { name: 'Turn off private chat' });
        expect(button).toHaveAttribute('data-private', 'true');
        expect(button).not.toHaveAttribute('aria-pressed');
        expect(button).toHaveAttribute('title', "Turn off private chat. This chat isn't saved to memory.");
        expect(button.querySelector('.lucide-eye-off')).not.toBeNull();
        expect(button.matches('.task-header-right .task-header-private[data-private="true"]')).toBe(true);
        expect(ruleBody('.task-header-right .task-header-private[data-private="true"]'))
            .toMatch(/background:\s*var\(--accent-fill\)/);
    });

    it('offers to turn private on, open eye and no on-state, on a normal chat', () => {
        renderChat(false);
        const button = screen.getByRole('button', { name: 'Turn on private chat' });
        expect(button).not.toHaveAttribute('data-private');
        expect(button).not.toHaveAttribute('aria-pressed');
        expect(button).toHaveAttribute('title', "Turn on private chat. It won't be saved to memory.");
        expect(button.querySelector('.lucide-eye')).not.toBeNull();
        expect(button.querySelector('.lucide-eye-off')).toBeNull();
    });
});
