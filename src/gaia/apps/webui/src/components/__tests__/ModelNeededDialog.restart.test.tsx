// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useModelNeededStore } from '../../stores/modelNeededStore';
import type { ModelNeed, SetupRunStatus } from '../../types';
import { ModelNeededDialog } from '../ModelNeededDialog';

vi.mock('../../services/api', async (importOriginal) => {
    const actual = await importOriginal<typeof import('../../services/api')>();
    return { ...actual, downloadSetupModel: vi.fn(), getSetupStatus: vi.fn(), cancelSetup: vi.fn() };
});
const mocked = vi.mocked(api);
const ask = (): ModelNeed => ({ job: 'chat', needed: true, model: { id: 'M', name: 'M', size_gb: 1, source: 's' }, disk_free_gb: 400 });

it('a late reply from a closed prompt leaves the re-started download alone', async () => {
    const begins: (() => void)[] = [];
    mocked.downloadSetupModel.mockImplementation(() => new Promise((r) => { begins.push(() => r({} as SetupRunStatus)); }));
    mocked.cancelSetup.mockResolvedValue({} as never);
    mocked.getSetupStatus.mockResolvedValue({ models: [] } as unknown as SetupRunStatus);
    useModelNeededStore.setState({ need: ask() });
    render(<ModelNeededDialog />);
    fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }));
    act(() => useModelNeededStore.setState({ need: ask() }));   // the user asks again
    fireEvent.click(screen.getByRole('button', { name: 'Download and continue' }));
    mocked.cancelSetup.mockClear();
    await act(async () => { begins[1](); });   // B's download begins and is the live one
    await act(async () => { begins[0](); });   // A's stale reply arrives
    expect(mocked.cancelSetup).not.toHaveBeenCalled();
});
