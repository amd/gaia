// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import { useModelStore } from '../../stores/modelStore';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { PreflightReport, SetupRunStatus } from '../../types';
import { COLD_BIG, QWEN, progress, runStatus } from '../setup/__tests__/fixtures';
import { NO_BACKENDS, NO_GPU_SPLIT } from '../../test/setupApiMocks';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError, setupConfirmFrom: real.setupConfirmFrom };
});
const mockedApi = vi.mocked(api);

const HARDWARE = {
    compatible: true, blockers: [], warnings: [], ram_gb: 96, gpu_name: 'AMD Radeon 8060S',
    npu_detected: true, disk_free_gb: 400, lemonade_running: true,
} as unknown as PreflightReport;

beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
    mockedApi.getOnboardingPreflight.mockResolvedValue(HARDWARE);
    mockedApi.getSetupStatus.mockResolvedValue({ state: 'idle' } as SetupRunStatus);
    mockedApi.getLemonadeTarget.mockResolvedValue({ offer: false, source: 'gaia' } as unknown as Awaited<ReturnType<typeof api.getLemonadeTarget>>);
    mockedApi.getSetupPlan.mockResolvedValue(COLD_BIG());
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getModelServer.mockResolvedValue(NO_BACKENDS);
    mockedApi.getStartScreen.mockResolvedValue({ cards: [], working_folder: null } as unknown as Awaited<ReturnType<typeof api.getStartScreen>>);
    mockedApi.getCloudPrices.mockResolvedValue({ provider: 'fireworks', models: [], source: null, note: '' } as unknown as Awaited<ReturnType<typeof api.getCloudPrices>>);
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

/** The checks stop on what they found: Continue, then the plan's start button. */
async function pastChecks() {
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }));
    return screen.findByRole('button', { name: /Download and start/ });
}

describe('SetupScreen', () => {
    it('a double click on "Download and start" starts one run, not two', async () => {
        mockedApi.startSetup.mockImplementation(() => new Promise(() => {}));
        render(<SetupScreen onReady={vi.fn()} />);
        const btn = await pastChecks();
        fireEvent.click(btn);
        fireEvent.click(btn);
        await act(async () => {});
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it('a double click on "Finish setup" starts one run, not two', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(
            runStatus([progress({ id: QWEN.id, state: 'ready', bytes_done: 1e9 })], { state: 'failed' }),
        );
        mockedApi.startSetup.mockImplementation(() => new Promise(() => {}));
        render(<SetupScreen onReady={vi.fn()} />);
        const btn = await screen.findByRole('button', { name: 'Finish setup' });
        fireEvent.click(btn);
        fireEvent.click(btn);
        await act(async () => {});
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
    });

    it('the lost-contact banner is translated in Arabic', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        mockedApi.getSetupStatus
            .mockResolvedValueOnce(runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e8 })]))
            .mockRejectedValue(new Error('Failed to fetch'));
        render(<SetupScreen onReady={vi.fn()} />);
        const banner = await screen.findByText(/انقطع الاتصال/, undefined, { timeout: 4000 });
        expect(banner).toHaveTextContent('Failed to fetch');
    });

    it('setup run again from Settings does not refetch everything on every parent render', async () => {
        const { rerender } = render(<SetupScreen onReady={vi.fn()} onClose={() => {}} />);
        await pastChecks();
        const before = mockedApi.getSetupPlan.mock.calls.length;
        rerender(<SetupScreen onReady={vi.fn()} onClose={() => {}} />);
        rerender(<SetupScreen onReady={vi.fn()} onClose={() => {}} />);
        await act(async () => {});
        expect(mockedApi.getSetupPlan.mock.calls.length).toBe(before);
        expect(mockedApi.getSetupStatus).toHaveBeenCalledTimes(1);
    });

    it('setup run again from Settings can be left when the plan fails to load', async () => {
        mockedApi.getSetupPlan.mockRejectedValue(new Error('backend 500'));
        const onClose = vi.fn();
        render(<SetupScreen onReady={vi.fn()} onClose={onClose} />);
        await screen.findByText('backend 500');
        expect(screen.queryByRole('button', { name: 'Back to GAIA' })).not.toBeNull();
    });

    it('offers Back to GAIA when opened from Settings, and not on first run', async () => {
        const { unmount } = render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByRole('button', { name: 'Continue' });
        expect(screen.queryByRole('button', { name: 'Back to GAIA' })).toBeNull();
        unmount();
        const onClose = vi.fn();
        render(<SetupScreen onReady={vi.fn()} onClose={onClose} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Back to GAIA' }));
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('opened from Settings, picks up a setup still running but not a finished one', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(runStatus([progress({ id: QWEN.id, state: 'ready', bytes_done: 1e9 })], { state: 'ready' }));
        render(<SetupScreen onReady={vi.fn()} onClose={vi.fn()} />);
        expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Start chatting' })).toBeNull();
    });

    it('setup run again for a cloud user continues on that provider, not a local download', async () => {
        useModelStore.setState({
            active: { provider: 'amd', model: 'amd.some-model', label: 'some-model · AMD LLM Gateway', remote: true, is_default: false },
        });
        render(<SetupScreen onReady={vi.fn()} onClose={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Continue' }));
        const amd = await screen.findByRole('radio', { name: /AMD LLM Gateway/ });
        expect(amd).toHaveAttribute('aria-checked', 'true');
        expect(screen.queryByRole('button', { name: /Download and start/ })).toBeNull();
    });

    it('setup run again for a local user still recommends the local download', async () => {
        useModelStore.setState({
            active: { provider: 'local', model: QWEN.id, label: QWEN.id, remote: false, is_default: true },
        });
        render(<SetupScreen onReady={vi.fn()} onClose={vi.fn()} />);
        expect(await pastChecks()).toBeInTheDocument();
    });

    it('setup run again on a cloud model with no provider tab does not jump to a key screen', async () => {
        // A model learned from Lemonade's list (e.g. opencode) is remote but has no GAIA tab.
        useModelStore.setState({
            active: { provider: 'local', model: 'opencode.big-model', label: 'big-model · opencode', remote: true, is_default: false },
        });
        render(<SetupScreen onReady={vi.fn()} onClose={vi.fn()} />);
        expect(await pastChecks()).toBeInTheDocument();
    });

    it('a failed setup-again run (download step) still offers a way back', async () => {
        // Run again, start, run fails before any model: no Not now anywhere.
        mockedApi.startSetup.mockResolvedValue({ ...runStatus([], { state: 'failed', error: 'boom' }) });
        const onClose = vi.fn();
        render(<SetupScreen onReady={vi.fn()} onClose={onClose} />);
        fireEvent.click(await pastChecks());
        await screen.findByText('Try again');
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Back to GAIA' })).not.toBeNull());
    });
});
