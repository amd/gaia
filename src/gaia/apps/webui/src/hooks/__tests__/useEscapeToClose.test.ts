// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useEscapeToClose } from '../useEscapeToClose';

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
