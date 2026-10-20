// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The active model, read while the backend is still starting.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import * as api from '../../services/api';
import { MODEL_RETRY_INITIAL_MS, resetModelRetryForTests, useModelStore } from '../modelStore';

const mocked = vi.mocked(api);
const ACTIVE = { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true } as never;

beforeEach(() => {
    vi.useFakeTimers();
    resetModelRetryForTests();
    mocked.listProviderModels.mockResolvedValue({ models: [] } as never);
    useModelStore.setState({ active: null, error: null, restoreNotice: null });
});

afterEach(() => {
    resetModelRetryForTests();
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe('modelStore.refresh', () => {
    it('retries a failed read with backoff until it answers', async () => {
        mocked.getActiveModel
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockRejectedValueOnce(new TypeError('Failed to fetch'))
            .mockResolvedValue(ACTIVE);
        await useModelStore.getState().refresh();
        expect(useModelStore.getState().error).toBe('Failed to fetch');

        await vi.advanceTimersByTimeAsync(MODEL_RETRY_INITIAL_MS);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(2);
        await vi.advanceTimersByTimeAsync(MODEL_RETRY_INITIAL_MS * 2);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(3);
        expect(useModelStore.getState().active).toEqual(ACTIVE);
        expect(useModelStore.getState().error).toBeNull();

        await vi.advanceTimersByTimeAsync(60_000);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(3);
    });

    it('retryNow reads at once after a failure, and is a no-op otherwise', async () => {
        mocked.getActiveModel.mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValue(ACTIVE);
        await useModelStore.getState().refresh();
        useModelStore.getState().retryNow();
        await vi.advanceTimersByTimeAsync(0);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(2);
        expect(useModelStore.getState().active).toEqual(ACTIVE);

        useModelStore.getState().retryNow();
        await vi.advanceTimersByTimeAsync(0);
        expect(mocked.getActiveModel).toHaveBeenCalledTimes(2);
    });
});
