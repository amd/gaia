// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The resource strip is on in every chat until closed, and one click brings it back. */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { GeneralSettings } from '../settings/GeneralSettings';
import { useChatStore } from '../../stores/chatStore';
import { useResourceMonitorStore } from '../../stores/resourceMonitorStore';
import type { ResourceUsage, Session } from '../../types';

const session: Session = {
    id: 'res-1', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

const USAGE: ResourceUsage = {
    interval_ms: 1000,
    ready: true,
    cpu: { percent: 20, ghz: 4.7, history: [20] },
    memory: { used_bytes: 1, total_bytes: 2, percent: 50, history: [50] },
    devices: [],
};

let resourceReads = 0;

function renderChat() {
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
    return render(<ChatView sessionId={session.id} />);
}

beforeEach(() => {
    window.localStorage.clear();
    useResourceMonitorStore.getState().reload();
    resourceReads = 0;
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        let body: unknown;
        if (url.pathname === '/api/system/resources') {
            resourceReads += 1;
            body = USAGE;
        } else if (url.pathname === `/api/sessions/${session.id}/messages`) {
            body = { messages: [], total: 0 };
        } else if (url.pathname.startsWith('/api/chat/permissions/')) {
            body = { session_id: session.id, mode: 'ask', grants: [] };
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

describe('resource usage in a chat', () => {
    it('is on by default, under the header, with a pressed toggle', async () => {
        renderChat();
        const region = await screen.findByRole('region', { name: 'Resource usage on this PC' });
        expect(region.previousElementSibling?.tagName).toBe('HEADER');
        await screen.findByText('4.70 GHz');
        const toggle = screen.getByRole('button', { name: 'Resource usage' });
        expect(toggle).toHaveAttribute('aria-pressed', 'true');
        expect(toggle).toHaveAttribute('title', 'Hide resource usage');
    });

    it('closes with the ✕, keeps focus on the toggle, stops polling, and stays closed', async () => {
        const { unmount } = renderChat();
        await screen.findByText('4.70 GHz');
        fireEvent.click(screen.getByRole('button', { name: 'Hide resource usage' }));

        expect(screen.queryByRole('region', { name: 'Resource usage on this PC' })).toBeNull();
        const toggle = screen.getByRole('button', { name: 'Resource usage' });
        expect(toggle).toHaveAttribute('aria-pressed', 'false');
        expect(toggle).toHaveAttribute('title', 'Show resource usage');
        expect(document.activeElement).toBe(toggle);
        expect(window.localStorage.getItem('gaia.resourceMonitor.visible')).toBe('0');

        const reads = resourceReads;
        await new Promise((r) => setTimeout(r, 1200));
        expect(resourceReads).toBe(reads);

        unmount();
        useResourceMonitorStore.getState().reload();
        renderChat();
        await screen.findByRole('button', { name: 'Resource usage' });
        expect(screen.queryByRole('region', { name: 'Resource usage on this PC' })).toBeNull();
    });

    it('comes back from the header toggle', async () => {
        renderChat();
        await screen.findByText('4.70 GHz');
        fireEvent.click(screen.getByRole('button', { name: 'Hide resource usage' }));
        fireEvent.click(screen.getByRole('button', { name: 'Resource usage' }));
        expect(await screen.findByRole('region', { name: 'Resource usage on this PC' })).toBeInTheDocument();
        expect(window.localStorage.getItem('gaia.resourceMonitor.visible')).toBe('1');
    });

    it('can be turned off and on in Settings › General', () => {
        render(<GeneralSettings />);
        const box = screen.getByRole('checkbox', { name: 'Show resource usage in chats' });
        expect(box).toBeChecked();
        fireEvent.click(box);
        expect(useResourceMonitorStore.getState().visible).toBe(false);
        fireEvent.click(box);
        expect(useResourceMonitorStore.getState().visible).toBe(true);
    });

    it('starts shown when the browser blocks storage', () => {
        window.localStorage.setItem('gaia.resourceMonitor.visible', '0');
        vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
        useResourceMonitorStore.getState().reload();
        expect(useResourceMonitorStore.getState().visible).toBe(true);
    });
});
