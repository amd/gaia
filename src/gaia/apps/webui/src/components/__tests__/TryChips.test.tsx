// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useChatStore } from '../../stores/chatStore';
import { TryChips } from '../TryChips';

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

describe('TryChips', () => {
    it('fills the English prompt in English', () => {
        const onPick = vi.fn();
        render(<TryChips onPick={onPick} />);
        fireEvent.click(screen.getByRole('button', { name: /^Recipe:/ }));
        expect(onPick).toHaveBeenCalledWith('Give me a banana bread recipe');
    });

    it('labels and fills every chip in Arabic (#7203)', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        const onPick = vi.fn();
        render(<TryChips onPick={onPick} />);
        const chips = screen.getAllByRole('button');
        expect(chips).toHaveLength(7);
        for (const chip of chips) {
            expect(chip.textContent).toMatch(/[؀-ۿ]/);
            fireEvent.click(chip);
        }
        expect(onPick).toHaveBeenCalledTimes(7);
        for (const [prompt] of onPick.mock.calls) {
            expect(prompt).toMatch(/[؀-ۿ]/);
            expect(prompt).not.toMatch(/[A-Za-z]{2,}/);
        }
    });
});
