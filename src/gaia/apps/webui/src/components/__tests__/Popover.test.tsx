// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRef } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
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

    it('re-measures when its content arrives after opening', async () => {
        vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue(
            { top: 250, bottom: 280 } as DOMRect,
        );
        const height = vi.spyOn(HTMLDivElement.prototype, 'scrollHeight', 'get').mockReturnValue(40);
        const anchorRef = { current: null as HTMLButtonElement | null };
        const view = (items: number) => (
            <div>
                <button ref={anchorRef} type="button">Anchor</button>
                <Popover open onClose={() => {}} anchor={anchorRef} label="Menu">
                    {Array.from({ length: items }, (_, i) => <button key={i} type="button">Model {i}</button>)}
                </Popover>
            </div>
        );
        const { rerender } = render(view(1));
        expect(screen.getByRole('dialog', { name: 'Menu' })).toHaveClass('popover-above');

        height.mockReturnValue(400);
        rerender(view(12));
        await waitFor(() => expect(screen.getByRole('dialog', { name: 'Menu' })).toHaveClass('popover-below'));
    });

    it('flips above when asked for below but the space is above', () => {
        layout(800, 830, 300);
        render(<Harness placement="below" />);
        expect(screen.getByRole('dialog', { name: 'Menu' })).toHaveClass('popover-above');
    });

    it('adds a caller class without dropping its placement classes', () => {
        layout(700, 730, 300);
        const anchorRef = { current: null as HTMLButtonElement | null };
        render(
            <div>
                <button ref={anchorRef} type="button">Anchor</button>
                <Popover open onClose={() => {}} anchor={anchorRef} label="Menu" align="end" className="model-menu">
                    <button type="button">Item</button>
                </Popover>
            </div>,
        );
        expect(screen.getByRole('dialog', { name: 'Menu' })).toHaveClass('popover', 'popover-above', 'popover-end', 'model-menu');
    });
});

describe('Popover sideways shift', () => {
    function place(naturalLeft: { value: number }, width = 358, dir = 'ltr') {
        vi.stubGlobal('innerWidth', 390);
        vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue(
            { top: 700, bottom: 730 } as DOMRect,
        );
        vi.spyOn(HTMLDivElement.prototype, 'scrollHeight', 'get').mockReturnValue(300);
        vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockReturnValue(document.body);
        vi.spyOn(HTMLElement.prototype, 'offsetLeft', 'get').mockImplementation(() => naturalLeft.value);
        vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(width);
        document.documentElement.dir = dir;
    }

    afterEach(() => { document.documentElement.dir = ''; });

    it('measures from the shift on screen when it re-places before React commits one', () => {
        const natural = { value: 64 };
        place(natural);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        expect(menu.style.transform).toBe('translateX(-46px)');

        natural.value = 100;
        act(() => {
            window.dispatchEvent(new Event('resize'));
            window.dispatchEvent(new Event('resize'));
        });
        expect(menu.style.transform).toBe('translateX(-82px)');
    });

    it('keeps its shift when the painted box still carries the old one', () => {
        const natural = { value: 64 };
        place(natural);
        // A running transition paints the previous shift, whatever the inline style says.
        vi.spyOn(HTMLDivElement.prototype, 'getBoundingClientRect').mockReturnValue({ left: 18, right: 376 } as DOMRect);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        act(() => { window.dispatchEvent(new Event('resize')); });
        expect(menu.style.transform).toBe('translateX(-46px)');
    });

    it.each(['ltr', 'rtl'])('pulls a box past either window edge inside the gutter (%s)', (dir) => {
        const natural = { value: -40 };
        place(natural, 200, dir);
        render(<Harness />);
        const menu = screen.getByRole('dialog', { name: 'Menu' });
        expect(menu.style.transform).toBe('translateX(54px)');

        natural.value = 300;
        act(() => { window.dispatchEvent(new Event('resize')); });
        expect(menu.style.transform).toBe('translateX(-124px)');
    });

    it.each([['ltr', 14], ['rtl', -24]])('pins a box wider than the window to its inline start (%s)', (dir, left) => {
        const natural = { value: 0 };
        place(natural, 400, dir);
        render(<Harness />);
        expect(screen.getByRole('dialog', { name: 'Menu' }).style.transform).toBe(`translateX(${left}px)`);
    });
});
