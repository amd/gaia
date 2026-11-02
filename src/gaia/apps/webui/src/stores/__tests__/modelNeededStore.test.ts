// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { ensureModel, forgetModelChecks, modelKnownReady, useModelNeededStore } from '../modelNeededStore';

vi.mock('../../services/api');
const mocked = vi.mocked(api);

const MISSING = {
    job: 'vision' as const,
    needed: true,
    model: { id: 'Gemma-4-E4B-it-GGUF', name: 'Gemma 4 E4B', size_gb: 6, source: null },
    disk_free_gb: 400,
};

describe('a job that needs a model', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        forgetModelChecks();
    });

    it('goes ahead, and asks no more, once its model is here', async () => {
        mocked.getModelNeed.mockResolvedValue({ job: 'chat', needed: false, model: null, disk_free_gb: null });
        await expect(ensureModel('chat')).resolves.toBe(true);
        expect(modelKnownReady('chat')).toBe(true);
    });

    it('waits on the prompt, and Not now stops the job', async () => {
        mocked.getModelNeed.mockResolvedValue(MISSING);
        const answer = ensureModel('vision');
        await vi.waitFor(() => expect(useModelNeededStore.getState().need).toEqual(MISSING));
        useModelNeededStore.getState().settle(false);
        await expect(answer).resolves.toBe(false);
        expect(useModelNeededStore.getState().need).toBeNull();
    });

    it('continues the job once the download finished', async () => {
        mocked.getModelNeed.mockResolvedValue(MISSING);
        const answer = ensureModel('vision');
        await vi.waitFor(() => expect(useModelNeededStore.getState().need).not.toBeNull());
        useModelNeededStore.getState().settle(true);
        await expect(answer).resolves.toBe(true);
    });

    it('never blocks a job when the check fails or hangs', async () => {
        mocked.getModelNeed.mockRejectedValue(new Error('server down'));
        await expect(ensureModel('chat')).resolves.toBe(true);
        expect(modelKnownReady('chat')).toBe(false);

        vi.useFakeTimers();
        mocked.getModelNeed.mockReturnValue(new Promise(() => {}));
        const answer = ensureModel('chat');
        await vi.advanceTimersByTimeAsync(8000);
        await expect(answer).resolves.toBe(true);
        vi.useRealTimers();
    });
});
