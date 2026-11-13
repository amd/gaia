// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';

vi.mock('../../services/api');

const HOUR = 3600 * 1000;

beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] });
    vi.setSystemTime(new Date(2026, 10, 12, 15, 0));
    const stamp = new Date().toISOString();
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: [{
            id: 'a', title: 'Dinner ideas', created_at: stamp, updated_at: stamp,
            model: '', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false,
        }],
        currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, messageQueues: {},
    });
});

afterEach(() => vi.useRealTimers());

describe('Sidebar day headings', () => {
    it('moves a chat from Today to Yesterday when the app stays open past midnight', () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        expect(screen.getByText('Today')).toBeInTheDocument();
        act(() => { vi.advanceTimersByTime(30 * HOUR); });
        expect(screen.getByText('Yesterday')).toBeInTheDocument();
        expect(screen.queryByText('Today')).not.toBeInTheDocument();
    });
});
