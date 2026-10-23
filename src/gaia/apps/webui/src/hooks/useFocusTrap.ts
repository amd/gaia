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

/** True when `el` is inside a closed `<details>` within `container`, outside that details' summary. */
function inClosedDetails(el: HTMLElement, container: HTMLElement): boolean {
    for (let d = el.parentElement?.closest('details'); d && container.contains(d); d = d.parentElement?.closest('details')) {
        if (!d.open && !d.querySelector(':scope > summary')?.contains(el)) return true;
    }
    return false;
}

function isShown(el: HTMLElement, container: HTMLElement): boolean {
    const inert = el.closest('[inert]');
    if ((inert && container.contains(inert)) || inClosedDetails(el, container)) return false;
    // One native check in Chromium/Electron instead of a style read per ancestor.
    if (typeof el.checkVisibility === 'function') return el.checkVisibility({ visibilityProperty: true });
    if (getComputedStyle(el).visibility === 'hidden') return false;
    for (let n: HTMLElement | null = el; n && n !== container.parentElement; n = n.parentElement) {
        if (n.hidden || getComputedStyle(n).display === 'none') return false;
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
    const isGroupedRadio = (el: HTMLElement): el is HTMLInputElement =>
        el instanceof HTMLInputElement && el.type === 'radio' && el.name !== '';
    const stopByGroup = new Map<string, HTMLInputElement>();
    for (const el of items) {
        if (!isGroupedRadio(el)) continue;
        const stop = stopByGroup.get(el.name);
        if (!stop || (el.checked && !stop.checked)) stopByGroup.set(el.name, el);
    }
    const stops = new Set<HTMLElement>(stopByGroup.values());
    return items.filter((el) => !isGroupedRadio(el) || stops.has(el));
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
