// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConnectionBanner } from '../ConnectionBanner';
import { useChatStore } from '../../stores/chatStore';
import { followBackendLifecycle, resyncBackend, useBackendStore } from '../../stores/backendStore';
import { getApiBase } from '../../utils/apiBase';
import type { BackendLifecycle } from '../../types/agent';

const base: BackendLifecycle = {
    status: 'running', port: 5000, since: null, attempt: 0, maxRestarts: 3,
    nextAttemptAt: null, lastExit: null, lastError: null, apiBase: 'http://127.0.0.1:5000/api',
};

beforeEach(() => {
    useChatStore.setState({ backendConnected: false, systemStatus: null, currentSessionId: null, messages: [] });
    useBackendStore.setState({ lifecycle: null, restartError: null });
});

afterEach(() => {
    vi.useRealTimers();
    delete (window as { gaiaAPI?: unknown }).gaiaAPI;
});

describe('a crashed backend', () => {
    it('says it is restarting, with a running timer, never "check that GAIA is still running"', () => {
        vi.useFakeTimers({ now: 100_000 });
        useBackendStore.setState({ lifecycle: { ...base, status: 'restarting', since: 100_000 - 12_000, attempt: 1 } });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent('GAIA’s assistant stopped and is restarting…');
        expect(container.querySelector('bdi')?.textContent).toBe('0:12');
        act(() => { vi.advanceTimersByTime(3_000); });
        expect(container.querySelector('bdi')?.textContent).toBe('0:15');
        expect(container).not.toHaveTextContent(/still running/i);
        expect(screen.queryByRole('button')).toBeNull();
    });

    it('shows even while a chat is open', () => {
        useChatStore.setState({ currentSessionId: 's1', messages: [{ role: 'user', content: 'hi' }] as never });
        useBackendStore.setState({ lifecycle: { ...base, status: 'restarting', since: Date.now() } });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent('restarting');
    });

    it('offers Restart GAIA once restarts run out, and the button restarts the backend', async () => {
        const restarting = { ...base, status: 'restarting' as const, since: Date.now(), attempt: 1 };
        const restart = vi.fn(async () => restarting);
        window.gaiaAPI = { backend: { restart, getState: vi.fn(), onState: vi.fn() } } as never;
        useBackendStore.setState({ lifecycle: { ...base, status: 'stopped', since: Date.now(), lastExit: { code: 1, signal: null } } });
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent('couldn’t restart on its own');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Restart GAIA' })); });
        expect(restart).toHaveBeenCalledTimes(1);
        expect(container).toHaveTextContent('is restarting…');
    });

    it('says why Restart GAIA failed', async () => {
        window.gaiaAPI = { backend: { restart: vi.fn(async () => { throw new Error('IPC gone'); }), getState: vi.fn(), onState: vi.fn() } } as never;
        useBackendStore.setState({ lifecycle: { ...base, status: 'stopped', since: Date.now() } });
        const { container } = render(<ConnectionBanner />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Restart GAIA' })); });
        expect(container).toHaveTextContent('GAIA couldn’t restart its assistant: IPC gone');
    });

    it('an unreachable backend outside the desktop app no longer asks the user to check it', () => {
        const { container } = render(<ConnectionBanner />);
        expect(container).toHaveTextContent('Can’t reach GAIA’s assistant. GAIA keeps trying to reconnect.');
        expect(container).not.toHaveTextContent(/still running/i);
    });
});

describe('followBackendLifecycle', () => {
    it('moves requests to the restarted port and re-checks once it is back', async () => {
        let push: (s: BackendLifecycle) => void = () => {};
        window.gaiaAPI = {
            backend: {
                getState: vi.fn(async () => base),
                restart: vi.fn(),
                onState: (cb: (s: BackendLifecycle) => void) => { push = cb; return () => {}; },
            },
        } as never;
        const onBack = vi.fn();
        followBackendLifecycle(onBack);
        await act(async () => {});
        expect(onBack).not.toHaveBeenCalled();
        act(() => push({ ...base, status: 'restarting', since: Date.now(), attempt: 1 }));
        act(() => push({ ...base, port: 6123, apiBase: 'http://127.0.0.1:6123/api' }));
        expect(getApiBase()).toBe('http://127.0.0.1:6123/api');
        expect(onBack).toHaveBeenCalledTimes(1);
    });

    it('re-reads the port from the desktop app when asked, after a missed push (#7284)', async () => {
        window.gaiaAPI = {
            backend: {
                getState: vi.fn(async () => ({ ...base, port: 7321, apiBase: 'http://127.0.0.1:7321/api' })),
                restart: vi.fn(),
                onState: vi.fn(),
            },
        } as never;
        resyncBackend();
        await act(async () => {});
        expect(getApiBase()).toBe('http://127.0.0.1:7321/api');
    });

    it('keeps the current port when the re-read fails or names a non-loopback host', async () => {
        useBackendStore.getState().apply({ ...base, port: 7400, apiBase: 'http://127.0.0.1:7400/api' });
        const getState = vi.fn()
            .mockRejectedValueOnce(new Error('IPC gone'))
            .mockResolvedValueOnce({ ...base, port: 80, apiBase: 'http://evil.example:80/api' })
            .mockResolvedValueOnce({ ...base, status: 'restarting', port: null, apiBase: null });
        window.gaiaAPI = { backend: { getState, restart: vi.fn(), onState: vi.fn() } } as never;
        for (let i = 0; i < 3; i++) {
            resyncBackend();
            await act(async () => {});
            expect(getApiBase()).toBe('http://127.0.0.1:7400/api');
        }
        expect(getState).toHaveBeenCalledTimes(3);
    });

    it('does nothing outside the desktop app', () => {
        expect(() => resyncBackend()).not.toThrow();
    });

    it('refuses an API base that is not loopback', () => {
        expect(() => useBackendStore.getState().apply({ ...base, apiBase: 'http://evil.example:80/api' })).toThrow(/loopback/);
    });
});
