// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTrayBridge } from '../useTrayBridge';
import { useChatStore } from '../../stores/chatStore';
import type { TrayActivity, TrayCommand } from '../../types/agent';

let onCommand: ((c: TrayCommand) => void) | null;
const setActivity = vi.fn(async (_activity: TrayActivity) => undefined);
const unsubscribe = vi.fn();

beforeEach(() => {
    onCommand = null;
    setActivity.mockClear();
    unsubscribe.mockClear();
    useChatStore.setState({ runningSessionIds: [], isStreaming: false, streamingContent: '', settingsSection: null });
    Object.defineProperty(window, 'gaiaAPI', {
        configurable: true,
        value: {
            tray: {
                getConfig: vi.fn(),
                setConfig: vi.fn(),
                setActivity,
                onCommand: (cb: (c: TrayCommand) => void) => { onCommand = cb; return unsubscribe; },
            },
        },
    });
});
afterEach(() => { delete window.gaiaAPI; });

const actions = () => ({ openChat: vi.fn(), newChat: vi.fn(), refreshStatus: vi.fn() });

describe('useTrayBridge', () => {
    it('reports on mount and whenever what the tray shows changes', () => {
        renderHook(() => useTrayBridge(actions()));
        expect(setActivity).toHaveBeenCalledTimes(1);
        act(() => useChatStore.setState({ runningSessionIds: ['s1'] }));
        expect(setActivity).toHaveBeenCalledTimes(2);
        expect(setActivity.mock.calls[1][0]).toMatchObject({ running: [{ id: 's1' }] });
    });

    it('streamed text alone sends nothing', () => {
        renderHook(() => useTrayBridge(actions()));
        act(() => useChatStore.setState({ streamingContent: 'Hello' }));
        act(() => useChatStore.setState({ streamingContent: 'Hello there' }));
        expect(setActivity).toHaveBeenCalledTimes(1);
    });

    it('runs menu commands in the window', () => {
        const a = actions();
        renderHook(() => useTrayBridge(a));
        act(() => onCommand!({ type: 'open-chat', sessionId: 's9' }));
        act(() => onCommand!({ type: 'new-chat' }));
        act(() => onCommand!({ type: 'refresh-status' }));
        act(() => onCommand!({ type: 'open-settings', section: 'general' }));
        expect(a.openChat).toHaveBeenCalledWith('s9');
        expect(a.newChat).toHaveBeenCalled();
        expect(a.refreshStatus).toHaveBeenCalled();
        expect(useChatStore.getState().settingsSection).toBe('general');
    });

    it('stops listening on unmount', () => {
        const { unmount } = renderHook(() => useTrayBridge(actions()));
        unmount();
        expect(unsubscribe).toHaveBeenCalled();
        act(() => useChatStore.setState({ runningSessionIds: ['s2'] }));
        expect(setActivity).toHaveBeenCalledTimes(1);
    });

    it('does nothing in a browser', () => {
        delete window.gaiaAPI;
        expect(() => renderHook(() => useTrayBridge(actions()))).not.toThrow();
    });
});
