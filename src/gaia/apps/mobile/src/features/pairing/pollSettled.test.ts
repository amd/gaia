// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaApiError, GaiaNetworkError, GaiaPinMismatchError } from '@/api/errors';

import { isNetworkFailure, pollSettled } from './pollSettled';

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

/** A check whose answers the test hands out one at a time. */
function slowCheck() {
    const answers: ((state: string) => void)[] = [];
    const check = jest.fn(() => new Promise<string>((resolve) => answers.push(resolve)));
    return { check, answer: (state: string) => answers.shift()!(state) };
}

describe('pollSettled', () => {
    it('starts the next check only once the last has answered', async () => {
        const { check, answer } = slowCheck();
        const stop = pollSettled(check, 2000, (state) => state === 'pending', () => true);

        await jest.advanceTimersByTimeAsync(2000);
        expect(check).toHaveBeenCalledTimes(1);
        // That check is still out, so no other starts beside it.
        await jest.advanceTimersByTimeAsync(20_000);
        expect(check).toHaveBeenCalledTimes(1);

        answer('pending');
        await jest.advanceTimersByTimeAsync(2000);
        expect(check).toHaveBeenCalledTimes(2);
        stop();
    });

    it('stops on a final answer, and once stopped, but goes on after a failed check', async () => {
        const failing = jest.fn(() => Promise.reject(new Error('offline')));
        const stopFailing = pollSettled(failing, 2000, () => true, () => true);
        await jest.advanceTimersByTimeAsync(6000);
        expect(failing).toHaveBeenCalledTimes(3);
        stopFailing();
        await jest.advanceTimersByTimeAsync(6000);
        expect(failing).toHaveBeenCalledTimes(3);

        const { check, answer } = slowCheck();
        pollSettled(check, 2000, (state) => state === 'pending', () => true);
        await jest.advanceTimersByTimeAsync(2000);
        answer('paired');
        await jest.advanceTimersByTimeAsync(10_000);
        expect(check).toHaveBeenCalledTimes(1);

        // Stopped while a check is out: its answer starts no other.
        const late = slowCheck();
        const stopLate = pollSettled(late.check, 2000, () => true, () => true);
        await jest.advanceTimersByTimeAsync(2000);
        stopLate();
        late.answer('pending');
        await jest.advanceTimersByTimeAsync(10_000);
        expect(late.check).toHaveBeenCalledTimes(1);
    });
});

describe('isNetworkFailure', () => {
    it('tries again after the network failed, never after the PC or its certificate answered', () => {
        expect(isNetworkFailure(new GaiaNetworkError('https://192.168.1.20:4243/api/health', new Error('timeout')))).toBe(true);
        expect(isNetworkFailure(Object.assign(new Error("Couldn't reach the PC"), { code: 'network' }))).toBe(true);
        expect(isNetworkFailure(new GaiaPinMismatchError())).toBe(false);
        expect(isNetworkFailure(new GaiaApiError(500, 'boom'))).toBe(false);
        expect(isNetworkFailure(Object.assign(new Error('damaged'), { code: 'keychain' }))).toBe(false);
    });
});
