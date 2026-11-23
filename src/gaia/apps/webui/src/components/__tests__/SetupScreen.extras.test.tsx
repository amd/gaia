// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import * as api from '../../services/api';
import type { PreflightReport, SetupRunStatus } from '../../types';
import { EMBED, GEMMA, QWEN, WHISPER, plan, progress, runStatus } from '../setup/__tests__/fixtures';
import { AGENT_PROGRAMS, NO_BACKENDS, NO_GPU_SPLIT, SETTINGS } from '../../test/setupApiMocks';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError, setupConfirmFrom: real.setupConfirmFrom };
});
const mockedApi = vi.mocked(api);

const QUEUED_KEY = 'gaia.setup.queuedExtras';
const running = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e8 })]);
const ready: SetupRunStatus = { ...runStatus([progress({ id: QWEN.id, state: 'ready', bytes_done: 1e9 })]), state: 'ready' };
/** The main run finished; the extra it handed off is downloading on its own. */
const readyWhisperPulling: SetupRunStatus = {
    ...runStatus([
        progress({ id: QWEN.id, state: 'ready', bytes_done: 1e9 }),
        progress({ id: WHISPER.id, roles: ['speech'], state: 'downloading', bytes_done: 2e8, bytes_total: 1.6e9 }),
    ]),
    state: 'ready',
};

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem(QUEUED_KEY);
    mockedApi.getOnboardingPreflight.mockResolvedValue({ compatible: true, blockers: [] } as unknown as PreflightReport);
    mockedApi.getLemonadeTarget.mockResolvedValue({ offer: false, source: 'gaia' } as unknown as Awaited<ReturnType<typeof api.getLemonadeTarget>>);
    mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED, WHISPER]));
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getModelServer.mockResolvedValue(NO_BACKENDS);
    mockedApi.getSettings.mockResolvedValue(SETTINGS);
    mockedApi.getAgentPrograms.mockResolvedValue(AGENT_PROGRAMS);
    mockedApi.getStartScreen.mockResolvedValue({ cards: [] } as unknown as Awaited<ReturnType<typeof api.getStartScreen>>);
});

const lowDisk = () => {
    const detail = { code: 'low_disk', message: 'Needs 1.6 GB; only 1.0 GB free.', acknowledge: 'acknowledge_low_disk' };
    return new api.ApiError(detail.message, 409, detail.message, null, [], detail);
};

describe('optional model added during the download', () => {
    it('is downloaded on its own once the run ends, so setup reaches Ready without re-checking the chat model', async () => {
        let now: SetupRunStatus = running;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        mockedApi.downloadSetupModel.mockImplementation(async () => { now = readyWhisperPulling; return now; });

        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        now = ready;
        // A second `gaia init` run would load-check every model again after Whisper downloads.
        expect(await screen.findByRole('button', { name: 'Start chatting' }, { timeout: 4000 })).toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
        expect(mockedApi.downloadSetupModel).toHaveBeenCalledWith(WHISPER.id, expect.objectContaining({ keepTrying: false }));
        expect(screen.getByText(/Still downloading: Whisper/)).toBeInTheDocument();
    });

    it('reads Added, not Add, while it downloads', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(readyWhisperPulling);
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByRole('button', { name: 'Start chatting' });
        const rail = screen.getByRole('navigation', { name: 'Setup steps' });
        fireEvent.click(within(rail).getByRole('button', { name: /Download/ }));
        const box = await screen.findByRole('region', { name: 'Optional' });
        expect(within(box).queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
        expect(within(box).getByRole('button', { name: 'Added' })).toBeDisabled();
    });

    it('is still added after setup is closed and opened again', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(running);
        const first = render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        first.unmount();

        render(<SetupScreen onReady={vi.fn()} />);
        expect(await screen.findByRole('button', { name: 'Added' })).toBeInTheDocument();
    });

    it('declining the low-disk sheet for it says on the Ready screen that it was not downloaded', async () => {
        let now: SetupRunStatus = running;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        mockedApi.downloadSetupModel.mockRejectedValue(lowDisk());

        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        now = ready;
        fireEvent.click(await screen.findByRole('button', { name: 'Change models' }, { timeout: 4000 }));
        await act(async () => {});
        expect(await screen.findByText(/Not downloaded: Whisper/)).toBeInTheDocument();
        expect(localStorage.getItem(QUEUED_KEY)).toBeNull();
    });

    it('failing after Ready moves it from the still-downloading line to the not-downloaded one', async () => {
        let now: SetupRunStatus = readyWhisperPulling;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByRole('button', { name: 'Start chatting' });
        expect(screen.getByText(/Still downloading: Whisper/)).toBeInTheDocument();
        expect(screen.queryByText(/Not downloaded: Whisper/)).not.toBeInTheDocument();

        now = {
            ...readyWhisperPulling,
            models: (readyWhisperPulling.models ?? []).map((m) => (m.id === WHISPER.id ? { ...m, state: 'failed' } : m)),
        };
        expect(await screen.findByText(/Not downloaded: Whisper/, undefined, { timeout: 4000 })).toBeInTheDocument();
        expect(screen.queryByText(/Still downloading: Whisper/)).not.toBeInTheDocument();
        // The chat model that is on disk is never listed.
        expect(screen.queryByText(new RegExp(`Not downloaded:.*${QWEN.name}`))).not.toBeInTheDocument();
    });

    it('a failed extra is listed once, even when its hand-off was also refused', async () => {
        let now: SetupRunStatus = running;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        mockedApi.downloadSetupModel.mockRejectedValue(lowDisk());
        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        now = {
            ...ready,
            models: [...(ready.models ?? []), progress({ id: WHISPER.id, roles: ['speech'], state: 'failed', bytes_done: 0, bytes_total: 1.6e9 })],
        };
        fireEvent.click(await screen.findByRole('button', { name: 'Change models' }, { timeout: 4000 }));
        await act(async () => {});
        const line = await screen.findByText(/Not downloaded: Whisper/);
        expect(line.textContent?.match(/Whisper/g)).toHaveLength(1);
    });

    it('Download anyway on the low-disk sheet takes the extra off the not-downloaded line', async () => {
        let now: SetupRunStatus = running;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        mockedApi.downloadSetupModel.mockRejectedValueOnce(lowDisk());
        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        now = ready;
        mockedApi.downloadSetupModel.mockImplementation(async () => { now = readyWhisperPulling; return now; });
        fireEvent.click(await screen.findByRole('button', { name: 'Download anyway' }, { timeout: 4000 }));
        await act(async () => {});
        expect(mockedApi.downloadSetupModel).toHaveBeenCalledTimes(2);
        expect(mockedApi.downloadSetupModel.mock.calls[1][1]).toMatchObject({ acknowledgeLowDisk: true });
        expect(screen.queryByText(/Not downloaded: Whisper/)).not.toBeInTheDocument();
        expect(mockedApi.startSetup).not.toHaveBeenCalled();
    });
});

describe('stopping with only the optional extras left', () => {
    /** Cancel › Stop while Whisper downloads: the required models are on disk, unchecked. */
    const stopped = runStatus([
        progress({ id: EMBED.id, roles: ['embedding'], state: 'unchecked', bytes_done: 0.3e9, bytes_total: 0.3e9 }),
        progress({ id: QWEN.id, state: 'unchecked', bytes_done: 1e9, bytes_total: 1e9 }),
        progress({ id: WHISPER.id, roles: ['speech'], state: 'cancelled', bytes_done: 5.6e8, bytes_total: 1.6e9 }),
    ], { state: 'cancelled' });

    it('offers to finish without them, and says on Ready they were not downloaded', async () => {
        mockedApi.getSetupStatus.mockResolvedValue(stopped);
        mockedApi.startSetup.mockResolvedValue(ready);
        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Finish without the extras' }));
        expect(await screen.findByRole('button', { name: 'Start chatting' })).toBeInTheDocument();
        expect(mockedApi.startSetup).toHaveBeenCalledTimes(1);
        expect(mockedApi.startSetup.mock.calls[0][2]?.extraModels ?? []).not.toContain(WHISPER.id);
        expect(screen.getByText(/Not downloaded: Whisper/)).toBeInTheDocument();
    });

    it("doesn't mark the Download step as failed after the user's own Stop", async () => {
        mockedApi.getSetupStatus.mockResolvedValue(stopped);
        render(<SetupScreen onReady={vi.fn()} />);
        await screen.findByRole('button', { name: 'Resume all' });
        const rail = within(screen.getByRole('navigation', { name: 'Setup steps' }));
        expect(rail.getByText('Download').closest('.setup-rail-step')).not.toHaveClass('is-failed');
    });
});
