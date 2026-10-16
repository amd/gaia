// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef } from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Popover } from '../Popover';

function Harness({ placement }: { placement?: 'above' | 'below' }) {
    const anchor = useRef<HTMLButtonElement>(null);
    return (
        <div>
            <button ref={anchor} type="button">Anchor</button>
            <Popover open onClose={() => {}} anchor={anchor} label="Menu" placement={placement}>
                <button type="button">Item</button>
            </Popover>
        </div>
    );
}

function layout(anchorTop: number, anchorBottom: number, menuHeight: number) {
    vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue(
        { top: anchorTop, bottom: anchorBottom } as DOMRect,
    );
    vi.spyOn(HTMLDivElement.prototype, 'scrollHeight', 'get').mockReturnValue(menuHeight);
}

beforeEach(() => {
    vi.stubGlobal('innerHeight', 960);
});

afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

describe('Popover placement', () => {
    it('opens above when the menu fits there', () => {
        layout(700, 730, 300);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        expect(menu).toHaveClass('popover-above');
        expect(menu.style.getPropertyValue('--popover-room')).toBe('686px');
    });

    it('flips below when there is not enough room above', () => {
        layout(250, 280, 400);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        expect(menu).toHaveClass('popover-below');
        expect(menu.style.getPropertyValue('--popover-room')).toBe('666px');
    });

    it('stays put and caps its height when neither side has more room', () => {
        layout(500, 530, 800);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        expect(menu).toHaveClass('popover-above');
        expect(menu.style.getPropertyValue('--popover-room')).toBe('486px');
    });

    it('flips above when asked for below but the space is above', () => {
        layout(800, 830, 300);
        render(<Harness placement="below" />);
        expect(screen.getByRole('dialog', { name: 'Menu' })).toHaveClass('popover-above');
    });
});
