// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act } from 'react';
import { BackHandler } from 'react-native';
import { createRoot } from 'test-renderer';
import { useBackStep } from './useBackStep';

function Flow({ step }: { step: () => boolean }) {
    useBackStep(step);
    return null;
}

describe('useBackStep', () => {
    it('registers once and asks the latest step, so a handler added later (a sheet) still goes first', async () => {
        const added: (() => boolean)[] = [];
        const spy = jest.spyOn(BackHandler, 'addEventListener').mockImplementation((_e, h) => {
            added.push(h as () => boolean);
            return { remove: () => void added.splice(added.indexOf(h as () => boolean), 1) };
        });
        const first = jest.fn(() => true);
        const second = jest.fn(() => false);
        const r = createRoot();
        await act(async () => {
            r.render(<Flow step={first} />);
        });
        await act(async () => {
            r.render(<Flow step={second} />);
        });
        expect(added).toHaveLength(1);
        expect(added[0]()).toBe(false);
        expect(second).toHaveBeenCalledTimes(1);
        expect(first).not.toHaveBeenCalled();
        await act(async () => {
            r.unmount();
        });
        expect(added).toHaveLength(0);
        spy.mockRestore();
    });
});
