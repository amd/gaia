// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../services/api';
import { useModelNeededStore } from '../stores/modelNeededStore';
import type { SetupRunStatus } from '../types';
import { ModelNeededDialog } from '../components/ModelNeededDialog';
import { progress, runStatus } from '../components/setup/__tests__/fixtures';

vi.mock('../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../services/api')>();
    return { ...actual, downloadSetupModel: vi.fn(), getSetupStatus: vi.fn(), cancelSetup: vi.fn() };
});
const mocked = vi.mocked(api);

beforeEach(() => { vi.clearAllMocks(); vi.useFakeTimers({ shouldAdvanceTime: true }); });
afterEach(() => { vi.useRealTimers(); act(() => useModelNeededStore.setState({ need: null })); });

describe('repro: ModelNeededDialog status blip', () => {
    it('one failed status read does not abandon a download that is still running', async () => {
        mocked.downloadSetupModel.mockResolvedValue({} as SetupRunStatus);
        mocked.getSetupStatus
            .mockRejectedValueOnce(new Error('Failed to fetch'))
            .mockResolvedValue(runStatus([progress({ id: 'm', state: 'ready' })]));
        useModelNeededStore.setState({
            need: { job: 'chat', needed: true, model: { id: 'm', name: 'M', size_gb: 6, source: null }, disk_free_gb: 400 },
        });
        render(<ModelNeededDialog />);
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Download and continue' })); });
        await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
        expect(useModelNeededStore.getState().need).toBeNull();
    });
});
