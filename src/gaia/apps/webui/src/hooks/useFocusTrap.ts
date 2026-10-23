// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, type RefObject } from 'react';

const CANDIDATES = [
    'a[href]', 'area[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
    'iframe', 'summary', '[contenteditable]:not([contenteditable="false"])', '[tabindex]',
].join(', ');

type Container = RefObject<HTMLElement | null>;

/** Open traps, most recently opened last. */
const traps: Container[] = [];

/** The trap that owns Tab: the newest, or a trap nested inside it (children mount first). */
function innermost(): Container | undefined {
    let top = traps[traps.length - 1];
    for (let moved = true; moved;) {
        moved = false;
        for (const t of traps) {
            if (t !== top && t.current && top?.current?.contains(t.current)) {
                top = t;
                moved = true;
            }
        }
    }
    return top;
}

function isShown(el: HTMLElement, container: HTMLElement): boolean {
    if (getComputedStyle(el).visibility === 'hidden') return false;
    for (let n: HTMLElement | null = el; n && n !== container.parentElement; n = n.parentElement) {
        if (n.hidden || n.hasAttribute('inert') || getComputedStyle(n).display === 'none') return false;
    }
    return true;
}

/** The elements Tab visits inside `container`, in DOM order. */
function tabbableIn(container: HTMLElement): HTMLElement[] {
    const items = Array.from(container.querySelectorAll<HTMLElement>(CANDIDATES)).filter((el) => {
        const tabindex = el.getAttribute('tabindex');
        if (tabindex !== null && Number(tabindex) < 0) return false;
        if (el.matches(':disabled')) return false;
        return isShown(el, container);
    });
    // Tab stops once per radio group: on the checked radio, else the first.
    return items.filter((el) => {
        if (!(el instanceof HTMLInputElement) || el.type !== 'radio' || !el.name) return true;
        const group = items.filter((o): o is HTMLInputElement =>
            o instanceof HTMLInputElement && o.type === 'radio' && o.name === el.name);
        const checked = group.find((o) => o.checked);
        return checked ? el === checked : el === group[0];
    });
}

/**
 * Keep Tab and Shift+Tab inside a modal while `active` is true: they wrap at
 * either end. The focusable list is re-read on every key press, so content
 * that changes while open is covered. Escape and returning focus on close stay
 * with the caller.
 */
export function useFocusTrap(containerRef: Container, active: boolean): void {
    useEffect(() => {
        if (!active) return;
        const self = containerRef;
        traps.push(self);
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key !== 'Tab' || e.defaultPrevented || innermost() !== self) return;
            const container = containerRef.current;
            if (!container) return;
            const items = tabbableIn(container);
            if (items.length === 0) {
                e.preventDefault();
                return;
            }
            const focused = document.activeElement;
            const inside = focused && container.contains(focused) && focused !== container ? focused : null;
            // Measured by position, so a focused tabindex=-1 element still wraps at the ends.
            const after = (a: Element, b: Element) =>
                !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
            const atStart = !!inside && !items.some((el) => after(el, inside));
            const atEnd = !!inside && !items.some((el) => after(inside, el));
            if (e.shiftKey && (!inside || atStart)) {
                e.preventDefault();
                items[items.length - 1].focus();
            } else if (!e.shiftKey && (!inside || atEnd)) {
                e.preventDefault();
                items[0].focus();
            }
        };
        document.addEventListener('keydown', onKeyDown);
        return () => {
            document.removeEventListener('keydown', onKeyDown);
            traps.splice(traps.indexOf(self), 1);
        };
    }, [containerRef, active]);
}
