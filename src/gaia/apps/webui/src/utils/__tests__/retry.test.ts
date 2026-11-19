// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { retryUntilOk } from '../retry';

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

const failing = (times: number) => {
    let n = 0;
    return vi.fn(async () => {
        n += 1;
        if (n <= times) throw new Error(`fail ${n}`);
    });
};

describe('retryUntilOk', () => {
    it('backs off, doubling up to the cap, until the task works', async () => {
        const task = failing(4);
        const r = retryUntilOk(task, { initialMs: 100, maxMs: 300 });
        await vi.advanceTimersByTimeAsync(0);
        expect(task).toHaveBeenCalledTimes(1);
        await vi.advanceTimersByTimeAsync(100);
        expect(task).toHaveBeenCalledTimes(2);
        await vi.advanceTimersByTimeAsync(200);
        expect(task).toHaveBeenCalledTimes(3);
        await vi.advanceTimersByTimeAsync(299);
        expect(task).toHaveBeenCalledTimes(3);
        await vi.advanceTimersByTimeAsync(1);
        expect(task).toHaveBeenCalledTimes(4);
        await vi.advanceTimersByTimeAsync(300);
        expect(task).toHaveBeenCalledTimes(5);
        expect(r.done()).toBe(true);
        await vi.advanceTimersByTimeAsync(10_000);
        expect(task).toHaveBeenCalledTimes(5);
    });

    it('kick retries now instead of waiting out the backoff', async () => {
        const task = failing(1);
        const r = retryUntilOk(task, { initialMs: 10_000 });
        await vi.advanceTimersByTimeAsync(0);
        r.kick();
        await vi.advanceTimersByTimeAsync(0);
        expect(task).toHaveBeenCalledTimes(2);
        expect(r.done()).toBe(true);
    });

    it('kick does nothing once done or while a try is in flight', async () => {
        let release!: () => void;
        const task = vi.fn(() => new Promise<void>((r) => { release = r; }));
        const r = retryUntilOk(task);
        r.kick();
        expect(task).toHaveBeenCalledTimes(1);
        release();
        await vi.advanceTimersByTimeAsync(0);
        r.kick();
        expect(task).toHaveBeenCalledTimes(1);
    });

    it('stop ends the retries', async () => {
        const task = failing(100);
        const onError = vi.fn();
        const r = retryUntilOk(task, { initialMs: 100, onError });
        await vi.advanceTimersByTimeAsync(0);
        r.stop();
        await vi.advanceTimersByTimeAsync(10_000);
        expect(task).toHaveBeenCalledTimes(1);
        expect(onError).toHaveBeenCalledExactlyOnceWith(expect.any(Error), 1);
    });
});
