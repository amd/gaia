// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { isEscapeClaimed, useEscapeLayer, useEscapeToClose } from '../useEscapeToClose';

function pressEscape(): KeyboardEvent {
    const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    document.body.dispatchEvent(e);
    return e;
}

describe('useEscapeToClose', () => {
    const listeners: Array<(e: KeyboardEvent) => void> = [];
    afterEach(() => {
        listeners.splice(0).forEach((l) => window.removeEventListener('keydown', l));
    });

    it('closes an open modal and claims the key so a streaming reply is not stopped', () => {
        const onClose = vi.fn();
        // Stands in for ChatView, which stops the reply on an unclaimed Escape.
        const stopReply = vi.fn();
        const chatView = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) stopReply(); };
        listeners.push(chatView);
        window.addEventListener('keydown', chatView);
        renderHook(() => useEscapeToClose(true, onClose));

        const e = pressEscape();

        expect(onClose).toHaveBeenCalledOnce();
        expect(e.defaultPrevented).toBe(true);
        expect(stopReply).not.toHaveBeenCalled();
    });

    it('does nothing while the modal is closed', () => {
        const onClose = vi.fn();
        renderHook(() => useEscapeToClose(false, onClose));

        const e = pressEscape();

        expect(onClose).not.toHaveBeenCalled();
        expect(e.defaultPrevented).toBe(false);
    });

    it('stops listening once unmounted', () => {
        const onClose = vi.fn();
        const { unmount } = renderHook(() => useEscapeToClose(true, onClose));
        unmount();

        pressEscape();

        expect(onClose).not.toHaveBeenCalled();
    });
});

describe('isEscapeClaimed', () => {
    function escapeOn(target: Element): KeyboardEvent {
        let seen: KeyboardEvent | null = null;
        const grab = (e: KeyboardEvent) => { seen = e; };
        window.addEventListener('keydown', grab, true);
        target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        window.removeEventListener('keydown', grab, true);
        return seen!;
    }

    afterEach(() => { document.body.innerHTML = ''; });

    it('is unclaimed with nothing open', () => {
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(false);
    });

    it('is claimed while any layer is open, and released once every layer closes', () => {
        const a = renderHook(() => useEscapeLayer(true));
        const b = renderHook(() => useEscapeLayer(true));
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(true);
        a.unmount();
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(true);
        b.unmount();
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(false);
    });

    it('counts a layer only while it is open', () => {
        const { rerender, unmount } = renderHook(({ open }) => useEscapeLayer(open), { initialProps: { open: false } });
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(false);
        rerender({ open: true });
        rerender({ open: true });
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(true);
        rerender({ open: false });
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(false);
        unmount();
        expect(isEscapeClaimed(escapeOn(document.body))).toBe(false);
    });

    it('is claimed for a field marked data-claims-escape, and anything inside one', () => {
        document.body.innerHTML = '<div data-claims-escape><input id="inner" /></div><textarea id="composer"></textarea>';
        expect(isEscapeClaimed(escapeOn(document.getElementById('inner')!))).toBe(true);
        expect(isEscapeClaimed(escapeOn(document.getElementById('composer')!))).toBe(false);
    });

    it('keeps a busy dialog as a layer even while its Escape is disabled', () => {
        const onClose = vi.fn();
        const { unmount } = renderHook(() => useEscapeToClose(true, onClose, false));
        const e = escapeOn(document.body);
        expect(onClose).not.toHaveBeenCalled();
        expect(isEscapeClaimed(e)).toBe(true);
        unmount();
    });
});

describe('nested overlays', () => {
    it('closes only the most recently opened overlay, one per Escape', () => {
        const closeSettings = vi.fn();
        const closeMenu = vi.fn();
        const settings = renderHook(() => useEscapeToClose(true, closeSettings));
        const menu = renderHook(() => useEscapeToClose(true, closeMenu));

        pressEscape();
        expect(closeMenu).toHaveBeenCalledOnce();
        expect(closeSettings).not.toHaveBeenCalled();

        menu.unmount();
        pressEscape();
        expect(closeSettings).toHaveBeenCalledOnce();
        expect(closeMenu).toHaveBeenCalledOnce();
        settings.unmount();
    });

    it('lets the outer overlay close again once an inner one closes out of order', () => {
        const closeA = vi.fn();
        const closeB = vi.fn();
        const closeC = vi.fn();
        const a = renderHook(() => useEscapeToClose(true, closeA));
        const b = renderHook(() => useEscapeToClose(true, closeB));
        const c = renderHook(() => useEscapeToClose(true, closeC));

        b.unmount();
        pressEscape();
        expect(closeC).toHaveBeenCalledOnce();
        c.unmount();
        pressEscape();
        expect(closeA).toHaveBeenCalledOnce();
        expect(closeB).not.toHaveBeenCalled();
        a.unmount();
    });

    it('a busy top overlay swallows Escape instead of letting the one below close', () => {
        const closeSettings = vi.fn();
        const closeBusy = vi.fn();
        const settings = renderHook(() => useEscapeToClose(true, closeSettings));
        const busy = renderHook(() => useEscapeToClose(true, closeBusy, false));

        const e = pressEscape();

        expect(e.defaultPrevented).toBe(true);
        expect(closeBusy).not.toHaveBeenCalled();
        expect(closeSettings).not.toHaveBeenCalled();
        busy.unmount();
        settings.unmount();
    });

    it('calls the latest close callback without re-registering the overlay', () => {
        const first = vi.fn();
        const second = vi.fn();
        const { rerender, unmount } = renderHook(({ cb }) => useEscapeToClose(true, cb), { initialProps: { cb: first } });
        rerender({ cb: second });

        pressEscape();

        expect(first).not.toHaveBeenCalled();
        expect(second).toHaveBeenCalledOnce();
        unmount();
    });

    it('leaves an overlay that handles Escape itself to do so', () => {
        const closeSettings = vi.fn();
        const settings = renderHook(() => useEscapeToClose(true, closeSettings));
        const popover = renderHook(() => useEscapeLayer(true));

        const e = pressEscape();

        expect(e.defaultPrevented).toBe(true);
        expect(closeSettings).not.toHaveBeenCalled();
        popover.unmount();
        settings.unmount();
    });
});
