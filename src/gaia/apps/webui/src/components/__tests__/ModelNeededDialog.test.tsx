// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useModelNeededStore } from '../../stores/modelNeededStore';
import type { ModelNeed, SetupRunStatus } from '../../types';
import { ModelNeededDialog } from '../ModelNeededDialog';

vi.mock('../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/api')>();
    return { ...actual, downloadSetupModel: vi.fn(), getSetupStatus: vi.fn(), cancelSetup: vi.fn() };
});
const mocked = vi.mocked(api);

function ask(freeGb: number | null): ModelNeed {
    return {
        job: 'chat',
        needed: true,
        model: { id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma 4 E4B', size_gb: 6, source: 'huggingface.co/x/y' },
        disk_free_gb: freeGb,
    };
}

describe('the missing-model prompt', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocked.downloadSetupModel.mockResolvedValue({} as SetupRunStatus);
    });

    afterEach(() => {
        vi.useRealTimers();
        act(() => useModelNeededStore.setState({ need: null }));
    });

    it('downloads without overriding the disk check when the model fits', () => {
        useModelNeededStore.setState({ need: ask(400) });
        render(<ModelNeededDialog />);
        expect(screen.queryByRole('alert')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
        expect(mocked.downloadSetupModel).toHaveBeenCalledWith('Gemma-4-E4B-it-GGUF', { acknowledgeLowDisk: false });
    });

    it('warns before a download the disk cannot hold, and only then overrides', () => {
        useModelNeededStore.setState({ need: ask(2) });
        render(<ModelNeededDialog />);
        expect(screen.getByRole('alert')).toHaveTextContent('Needs 6.0 GB; only 2.0 GB free. Free 4.0 GB first');
        expect(screen.queryByRole('button', { name: 'Download and continue' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Download anyway' }));
        expect(mocked.downloadSetupModel).toHaveBeenCalledWith('Gemma-4-E4B-it-GGUF', { acknowledgeLowDisk: true });
    });

    it('offers Download anyway when the server finds the disk fuller than the prompt knew', async () => {
        const detail = { code: 'low_disk', message: 'Needs 6.0 GB; only 1.0 GB free on C:.' };
        mocked.downloadSetupModel.mockRejectedValueOnce(new api.ApiError(detail.message, 409, detail.message, null, [], detail));
        useModelNeededStore.setState({ need: ask(400) });
        render(<ModelNeededDialog />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
        });
        expect(screen.getByRole('alert')).toHaveTextContent('only 1.0 GB free');
        fireEvent.click(screen.getByRole('button', { name: 'Download anyway' }));
        expect(mocked.downloadSetupModel).toHaveBeenLastCalledWith('Gemma-4-E4B-it-GGUF', { acknowledgeLowDisk: true });
    });

    it('cancels a download that only began after Not now', async () => {
        let begin!: () => void;
        mocked.downloadSetupModel.mockImplementation(() => new Promise((r) => { begin = () => r({} as SetupRunStatus); }));
        mocked.cancelSetup.mockResolvedValue({} as never);
        useModelNeededStore.setState({ need: ask(400) });
        render(<ModelNeededDialog />);
        fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
        fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
        mocked.cancelSetup.mockClear();
        await act(async () => { begin(); });
        expect(mocked.cancelSetup).toHaveBeenCalledWith('Gemma-4-E4B-it-GGUF');
    });

    it('stops waiting on a download that never shows up', async () => {
        vi.useFakeTimers();
        mocked.getSetupStatus.mockResolvedValue({ models: [] } as unknown as SetupRunStatus);
        useModelNeededStore.setState({ need: ask(400) });
        render(<ModelNeededDialog />);
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
        });
        await act(async () => {
            await vi.advanceTimersByTimeAsync(16_000);
        });
        expect(screen.getByRole('alert')).toHaveTextContent('The download stopped.');
        expect(screen.getByRole('button', { name: 'Try again' })).toBeEnabled();
    });
});
