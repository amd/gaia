// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, type RefObject } from 'react';
import { tabbableIn } from './useFocusTrap';

/**
 * Move focus into a modal when it opens, and give it back when it closes.
 *
 * On open, focus goes to `initialFocus()` when given, otherwise to the first
 * element Tab would reach, otherwise to the container itself. Focus that is
 * already inside the container (an `autoFocus` field) is left where it is.
 * On close or unmount, focus returns to whatever had it before the modal
 * opened, if that element is still on the page.
 *
 * Pairs with `useFocusTrap`, which keeps Tab inside, and `useEscapeToClose`.
 */
export function useDialogFocus(
    containerRef: RefObject<HTMLElement | null>,
    isOpen: boolean,
    initialFocus?: () => HTMLElement | null | undefined,
): void {
    const initialRef = useRef(initialFocus);
    useEffect(() => {
        initialRef.current = initialFocus;
    });

    useEffect(() => {
        if (!isOpen) return;
        const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const container = containerRef.current;
        if (container && !container.contains(document.activeElement)) {
            const target = initialRef.current?.() ?? tabbableIn(container)[0];
            if (target) {
                target.focus();
            } else {
                if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
                container.focus();
            }
        }
        return () => {
            if (previous?.isConnected) previous.focus();
        };
    }, [containerRef, isOpen]);
}
