// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A download that GAIA refused to start, read alongside the status poll.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import * as api from '../../services/api';
import { resetSetupDownloads, useSetupDownloads } from '../setupDownloadsStore';

const mocked = vi.mocked(api);
const RUNNING = { state: 'running', models: [] } as never;
const IDLE = { state: 'idle', models: [] } as never;

beforeEach(() => {
    vi.useFakeTimers();
    resetSetupDownloads();
});

afterEach(() => {
    resetSetupDownloads();
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe('setupDownloads start failure', () => {
    it('keeps the refusal through status polls', async () => {
        mocked.downloadSetupModel.mockRejectedValue(new Error('Another download is running'));
        mocked.getSetupStatus.mockResolvedValue(RUNNING);
        await useSetupDownloads.getState().enqueue(['a']);
        expect(useSetupDownloads.getState().error).toBe('Another download is running');

        await useSetupDownloads.getState().refresh();
        expect(useSetupDownloads.getState().error).toBe('Another download is running');
    });

    it('clears the refusal on the next successful start', async () => {
        mocked.downloadSetupModel.mockRejectedValueOnce(new Error('refused')).mockResolvedValue(RUNNING);
        mocked.getSetupStatus.mockResolvedValue(IDLE);
        await useSetupDownloads.getState().enqueue(['a', 'b']);
        expect(useSetupDownloads.getState().error).toBe('refused');

        await useSetupDownloads.getState().refresh();
        expect(mocked.downloadSetupModel).toHaveBeenCalledTimes(2);
        expect(useSetupDownloads.getState().error).toBeNull();
    });
});
