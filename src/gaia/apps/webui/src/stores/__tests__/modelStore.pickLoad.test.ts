// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Picking a local model starts its load; the store follows that load to the end.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api');

import * as api from '../../services/api';
import { PICK_LOAD_POLL_MS, resetModelRetryForTests, useModelStore } from '../modelStore';

const mocked = vi.mocked(api);
const QWEN = 'Qwen3.6-35B-A3B-GGUF';
const local = { provider: 'local', model: QWEN, label: 'Qwen', remote: false, is_default: false } as never;

beforeEach(() => {
    vi.useFakeTimers();
    resetModelRetryForTests();
    mocked.listSessions.mockResolvedValue({ sessions: [] } as never);
    useModelStore.setState({ active: null, pickLoad: null });
});

afterEach(() => {
    resetModelRetryForTests();
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe('picking a local model', () => {
    it('follows its load until it is ready', async () => {
        mocked.selectModel.mockResolvedValue(local);
        mocked.getWarmUp
            .mockResolvedValueOnce({ state: 'warming', elapsed_seconds: 1.2, model: QWEN } as never)
            .mockResolvedValueOnce({ state: 'warming', elapsed_seconds: 2.4, model: QWEN } as never)
            .mockResolvedValue({ state: 'ready', elapsed_seconds: 30, model: QWEN } as never);
        await useModelStore.getState().select(QWEN);
        await vi.advanceTimersByTimeAsync(0);
        expect(useModelStore.getState().pickLoad).toEqual({ model: QWEN, seconds: 1, error: null });
        await vi.advanceTimersByTimeAsync(PICK_LOAD_POLL_MS);
        expect(useModelStore.getState().pickLoad?.seconds).toBe(2);
        await vi.advanceTimersByTimeAsync(PICK_LOAD_POLL_MS);
        expect(useModelStore.getState().pickLoad).toBeNull();
    });

    it('keeps the reason a load failed, and can try again', async () => {
        const reason = 'Not enough free memory to load Qwen3.6-35B-A3B-GGUF.';
        mocked.selectModel.mockResolvedValue(local);
        mocked.getWarmUp.mockResolvedValue({ state: 'failed', elapsed_seconds: 3, model: QWEN, error: reason } as never);
        await useModelStore.getState().select(QWEN);
        await vi.advanceTimersByTimeAsync(0);
        expect(useModelStore.getState().pickLoad).toEqual({ model: QWEN, seconds: 3, error: { reason } });

        mocked.requestWarmUp.mockResolvedValue({ state: 'warming' } as never);
        mocked.getWarmUp.mockResolvedValue({ state: 'warming', elapsed_seconds: 0.5, model: QWEN } as never);
        await useModelStore.getState().retryPickLoad();
        await vi.advanceTimersByTimeAsync(0);
        expect(mocked.requestWarmUp).toHaveBeenCalledWith(true);
        expect(useModelStore.getState().pickLoad).toEqual({ model: QWEN, seconds: 0, error: null });
    });

    it('names a failure the backend gave no reason for, in the chosen language', async () => {
        mocked.selectModel.mockResolvedValue(local);
        mocked.getWarmUp.mockResolvedValue({ state: 'failed', elapsed_seconds: 2, model: QWEN, error: null } as never);
        await useModelStore.getState().select(QWEN);
        await vi.advanceTimersByTimeAsync(0);
        expect(useModelStore.getState().pickLoad?.error).toEqual({ key: 'banner.pickLoad.noReason', reason: '' });
    });

    it('says so when it loses track of the load', async () => {
        mocked.selectModel.mockResolvedValue(local);
        mocked.getWarmUp.mockRejectedValue(new Error('offline'));
        await useModelStore.getState().select(QWEN);
        await vi.advanceTimersByTimeAsync(0);
        expect(useModelStore.getState().pickLoad?.error).toEqual({ key: 'banner.pickLoad.lostTrack', reason: 'offline' });
    });

    it('ignores an older failure of another model', async () => {
        mocked.selectModel.mockResolvedValue(local);
        mocked.getWarmUp.mockResolvedValue({ state: 'failed', model: 'gemma4-it-e4b-FLM', error: 'old' } as never);
        await useModelStore.getState().select(QWEN);
        await vi.advanceTimersByTimeAsync(0);
        expect(useModelStore.getState().pickLoad).toBeNull();
    });

    it('waits on nothing for a cloud model', async () => {
        mocked.selectModel.mockResolvedValue({ provider: 'fireworks', model: 'fireworks.x', remote: true } as never);
        await useModelStore.getState().select('fireworks.x');
        await vi.advanceTimersByTimeAsync(PICK_LOAD_POLL_MS * 3);
        expect(mocked.getWarmUp).not.toHaveBeenCalled();
        expect(useModelStore.getState().pickLoad).toBeNull();
    });
});
