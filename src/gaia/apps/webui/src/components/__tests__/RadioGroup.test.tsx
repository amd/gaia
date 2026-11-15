// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { RadioGroup } from '../RadioGroup';
import { GeneralSettings } from '../settings/GeneralSettings';
import { useChatStore } from '../../stores/chatStore';

function Sizes({ start = 'Small', off = '' }: { start?: string; off?: string }) {
    const [size, setSize] = useState(start);
    return (
        <RadioGroup aria-label="Size">
            {['Small', 'Medium', 'Large'].map((s) => (
                <button key={s} type="button" role="radio" aria-checked={size === s} disabled={s === off} onClick={() => setSize(s)}>
                    {s}
                </button>
            ))}
        </RadioGroup>
    );
}

const tabStops = () => screen.getAllByRole('radio').filter((r) => r.tabIndex === 0).map((r) => r.textContent);
const press = (key: string) => fireEvent.keyDown(document.activeElement!, { key });

afterEach(() => {
    useChatStore.getState().setThemePreference('system');
});

describe('RadioGroup keys', () => {
    it('Right Arrow on Light in Settings > Appearance moves focus to Dark and picks it', () => {
        render(<GeneralSettings />);
        const group = screen.getByRole('radiogroup', { name: 'Appearance' });
        const light = screen.getByRole('radio', { name: 'Light' });
        expect(group.querySelectorAll('[role="radio"][tabindex="0"]')).toHaveLength(1);

        light.focus();
        press('ArrowRight');
        const dark = screen.getByRole('radio', { name: 'Dark' });
        expect(dark).toHaveFocus();
        expect(dark).toHaveAttribute('aria-checked', 'true');
        expect(dark.tabIndex).toBe(0);
        expect(light.tabIndex).toBe(-1);
    });

    it('keeps one Tab stop, on the chosen option or the first when none is chosen', () => {
        const { unmount } = render(<Sizes start="Medium" />);
        expect(tabStops()).toEqual(['Medium']);
        unmount();
        render(<Sizes start="" />);
        expect(tabStops()).toEqual(['Small']);
    });

    it('wraps, jumps with Home and End, and skips a disabled option', () => {
        render(<Sizes off="Medium" />);
        screen.getByRole('radio', { name: 'Small' }).focus();
        press('ArrowDown');
        expect(screen.getByRole('radio', { name: 'Large' })).toHaveFocus();
        press('ArrowRight');
        expect(screen.getByRole('radio', { name: 'Small' })).toHaveFocus();
        press('End');
        expect(screen.getByRole('radio', { name: 'Large' })).toHaveAttribute('aria-checked', 'true');
        press('Home');
        expect(screen.getByRole('radio', { name: 'Small' })).toHaveAttribute('aria-checked', 'true');
    });

    it('in right-to-left layout, Left Arrow moves to the next option', () => {
        render(<div dir="rtl"><Sizes /></div>);
        screen.getByRole('radio', { name: 'Small' }).focus();
        press('ArrowLeft');
        expect(screen.getByRole('radio', { name: 'Medium' })).toHaveFocus();
        press('ArrowRight');
        expect(screen.getByRole('radio', { name: 'Small' })).toHaveFocus();
    });
});
