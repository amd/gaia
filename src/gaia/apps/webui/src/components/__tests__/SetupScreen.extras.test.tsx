// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupScreen } from '../SetupScreen';
import * as api from '../../services/api';
import type { PreflightReport, SetupRunStatus } from '../../types';
import { EMBED, GEMMA, QWEN, WHISPER, plan, progress, runStatus } from '../setup/__tests__/fixtures';
import { NO_GPU_SPLIT } from '../../test/setupApiMocks';

vi.mock('../../services/api', async (importOriginal) => {
    const real = await importOriginal<typeof api>();
    const mocked = Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]));
    return { ...mocked, ApiError: real.ApiError, setupConfirmFrom: real.setupConfirmFrom };
});
const mockedApi = vi.mocked(api);

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getOnboardingPreflight.mockResolvedValue({ compatible: true, blockers: [] } as unknown as PreflightReport);
    mockedApi.getLemonadeTarget.mockResolvedValue({ offer: false, source: 'gaia' } as unknown as Awaited<ReturnType<typeof api.getLemonadeTarget>>);
    mockedApi.getSetupPlan.mockResolvedValue(plan([QWEN, GEMMA, EMBED, WHISPER]));
    mockedApi.getGpuMemory.mockResolvedValue(NO_GPU_SPLIT);
    mockedApi.getStartScreen.mockResolvedValue({ cards: [] } as unknown as Awaited<ReturnType<typeof api.getStartScreen>>);
});

describe('optional model added during the download', () => {
    it('declining the low-disk sheet for it says on the Ready screen that it was not downloaded', async () => {
        const running = runStatus([progress({ id: QWEN.id, state: 'downloading', bytes_done: 1e8 })]);
        const ready: SetupRunStatus = { ...runStatus([progress({ id: QWEN.id, state: 'ready', bytes_done: 1e9 })]), state: 'ready' };
        let now: SetupRunStatus = running;
        mockedApi.getSetupStatus.mockImplementation(async () => now);
        const detail = { code: 'low_disk', message: 'Needs 1.6 GB; only 1.0 GB free.', acknowledge: 'acknowledge_low_disk' };
        mockedApi.startSetup.mockRejectedValue(new api.ApiError(detail.message, 409, detail.message, null, [], detail));

        render(<SetupScreen onReady={vi.fn()} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
        now = ready;
        fireEvent.click(await screen.findByRole('button', { name: 'Change models' }, { timeout: 4000 }));
        await act(async () => {});
        // Whisper was never fetched, yet the user lands on Ready with nothing said.
        expect(await screen.findByText(/Not downloaded: Whisper/)).toBeInTheDocument();
    });
});
