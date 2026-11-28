// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StartupFailed } from '../StartupScreen';
import { useBackendStore } from '../../stores/backendStore';
import type { BackendLifecycle } from '../../types/agent';

const base: BackendLifecycle = {
    status: 'running', port: 5000, since: null, attempt: 0, maxRestarts: 3,
    nextAttemptAt: null, lastExit: null, lastError: null, apiBase: 'http://127.0.0.1:5000/api',
};

beforeEach(() => {
    useBackendStore.setState({ lifecycle: null, restartError: null });
});

afterEach(() => {
    delete (window as { gaiaAPI?: unknown }).gaiaAPI;
});

function mountBridge(restart: () => Promise<BackendLifecycle>) {
    window.gaiaAPI = { backend: { restart: vi.fn(restart), getState: vi.fn(), onState: vi.fn() } } as never;
    return (window.gaiaAPI as { backend: { restart: ReturnType<typeof vi.fn> } }).backend.restart;
}

describe('StartupFailed', () => {
    it('offers Restart GAIA for a backend still reported running: it started but never answered', async () => {
        const restart = mountBridge(async () => ({ ...base, status: 'restarting', since: Date.now(), attempt: 1 }));
        useBackendStore.setState({ lifecycle: { ...base, status: 'running' } });
        const { container } = render(<StartupFailed error="The backend did not answer within 30 s." onCheckAgain={() => {}} />);
        expect(container).toHaveTextContent('Restarting GAIA usually fixes this.');
        expect(container).not.toHaveTextContent('Closing GAIA and opening it again');
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Restart GAIA' })); });
        expect(restart).toHaveBeenCalledTimes(1);
    });

    it('still offers Restart GAIA once the backend gave up', () => {
        useBackendStore.setState({ lifecycle: { ...base, status: 'stopped', lastError: "GAIA's backend program is missing" } });
        render(<StartupFailed error="GAIA's backend program is missing" onCheckAgain={() => {}} />);
        expect(screen.getByRole('button', { name: 'Restart GAIA' })).not.toBeDisabled();
    });

    it('disables Restart GAIA while a restart is already running', () => {
        useBackendStore.setState({ lifecycle: { ...base, status: 'restarting', since: Date.now(), attempt: 1 } });
        render(<StartupFailed error="The backend did not answer within 30 s." onCheckAgain={() => {}} />);
        expect(screen.getByRole('button', { name: 'Restarting…' })).toBeDisabled();
    });

    it('outside the desktop app there is no Restart GAIA, only the reopen hint', () => {
        useBackendStore.setState({ lifecycle: null });
        const { container } = render(<StartupFailed error="The backend did not answer within 30 s." onCheckAgain={() => {}} />);
        expect(screen.queryByRole('button', { name: 'Restart GAIA' })).toBeNull();
        expect(container).toHaveTextContent('Closing GAIA and opening it again usually fixes this.');
        expect(screen.getByRole('button', { name: 'Check again' })).not.toBeDisabled();
    });
});
