// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import {
    useCallback, useEffect, useRef,
    type MutableRefObject, type PointerEvent as ReactPointerEvent, type RefObject, type WheelEvent as ReactWheelEvent,
} from 'react';

// Slack for fractional scrollTop at non-100% zoom.
const AT_BOTTOM_PX = 1;
const NEAR_BOTTOM_PX = 80;
// How long after wheel/key/touch input an upward scroll still counts as the user's.
const USER_SCROLL_MS = 500;
const SCROLL_UP_KEYS = new Set(['PageUp', 'ArrowUp', 'Home']);
const SCROLL_DOWN_KEYS = new Set(['PageDown', 'ArrowDown', 'End']);

export interface FollowScroll {
    /** True while the reader is scrolling, so an auto-follow scroll must wait. */
    userScrolling: () => boolean;
    /** Forget a recent scroll-up, e.g. when the reader asks to jump to the bottom. */
    clearUserScroll: () => void;
    /** Props for the scrolling list. */
    listProps: {
        onScroll: () => void;
        onWheel: (e: ReactWheelEvent) => void;
        onTouchMove: () => void;
        onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void;
    };
}

/**
 * Keep `followRef` true while the reader is at the bottom of `scrollRef`.
 * Smooth scrolls and scroll anchoring also move scrollTop up, so only the
 * reader's own input (wheel, keys, touch, scrollbar) turns following off.
 */
export function useFollowScroll(
    scrollRef: RefObject<HTMLElement | null>,
    followRef: MutableRefObject<boolean>,
    onDistance: (distFromBottom: number) => void,
): FollowScroll {
    const lastScrollTopRef = useRef(0);
    const lastDistRef = useRef(0);
    const userScrollAtRef = useRef(0);
    const scrollbarHeldRef = useRef(false);

    const userScrolling = useCallback(
        () => scrollbarHeldRef.current || Date.now() - userScrollAtRef.current < USER_SCROLL_MS,
        [],
    );
    const markUserScroll = useCallback(() => { userScrollAtRef.current = Date.now(); }, []);
    // Heading back down ends the hold, so a reader who returns to the bottom is followed at once.
    const clearUserScroll = useCallback(() => { userScrollAtRef.current = 0; }, []);

    const onScroll = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
        const lastDist = lastDistRef.current;
        const movedUp = el.scrollTop < lastScrollTopRef.current && userScrolling();
        lastScrollTopRef.current = el.scrollTop;
        lastDistRef.current = dist;
        if (dist <= AT_BOTTOM_PX) followRef.current = true;
        else if (movedUp) followRef.current = false;
        // Content growing above a reader keeps the distance, so only a real approach resumes.
        else if (dist <= NEAR_BOTTOM_PX && dist < lastDist) followRef.current = true;
        onDistance(dist);
    }, [scrollRef, followRef, onDistance, userScrolling]);

    const onWheel = useCallback((e: ReactWheelEvent) => {
        if (e.deltaY < 0) markUserScroll();
        else if (e.deltaY > 0) clearUserScroll();
    }, [markUserScroll, clearUserScroll]);

    // A press on the list itself is its scrollbar (either side, or an overlay one) or bare
    // padding; a press on content, like a card toggle, is not a scroll.
    const onPointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
        if (e.target === e.currentTarget) scrollbarHeldRef.current = true;
    }, []);

    useEffect(() => {
        const release = () => { scrollbarHeldRef.current = false; };
        const onKey = (e: KeyboardEvent) => {
            const t = e.target;
            if (t instanceof Element && t.closest('input, textarea, select, [contenteditable="true"]')) return;
            if (SCROLL_UP_KEYS.has(e.key) || (e.key === ' ' && e.shiftKey)) markUserScroll();
            else if (SCROLL_DOWN_KEYS.has(e.key) || e.key === ' ') clearUserScroll();
        };
        window.addEventListener('pointerup', release);
        window.addEventListener('pointercancel', release);
        window.addEventListener('keydown', onKey);
        return () => {
            window.removeEventListener('pointerup', release);
            window.removeEventListener('pointercancel', release);
            window.removeEventListener('keydown', onKey);
        };
    }, [markUserScroll, clearUserScroll]);

    // A card opening in the composer shrinks the list without a scroll event, hiding its last rows.
    useEffect(() => {
        const el = scrollRef.current;
        if (!el || typeof ResizeObserver === 'undefined') return;
        let height = el.clientHeight;
        const observer = new ResizeObserver(() => {
            const shrank = el.clientHeight < height;
            height = el.clientHeight;
            if (shrank && followRef.current && !userScrolling()) el.scrollTop = el.scrollHeight;
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, [scrollRef, followRef, userScrolling]);

    return { userScrolling, clearUserScroll, listProps: { onScroll, onWheel, onTouchMove: markUserScroll, onPointerDown } };
}
