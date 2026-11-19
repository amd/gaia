// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useEscapeLayer } from '../hooks/useEscapeToClose';
import './Popover.css';

interface PopoverProps {
    open: boolean;
    onClose: () => void;
    /** The button that toggles it; kept out of the outside-click check. */
    anchor: React.RefObject<HTMLElement | null>;
    label: string;
    placement?: 'above' | 'below';
    align?: 'start' | 'end';
    className?: string;
    children: ReactNode;
}

/** Anchor offset (6px) plus a margin so the panel never touches the window edge. */
const EDGE_GAP = 14;

/** A small anchored panel that closes on Escape or an outside click, and returns focus. */
export function Popover({ open, onClose, anchor, label, placement = 'above', align = 'start', className, children }: PopoverProps) {
    const ref = useRef<HTMLDivElement>(null);
    const [side, setSide] = useState(placement);
    const [room, setRoom] = useState<number | null>(null);
    const [shift, setShift] = useState(0);

    // Flip when the preferred side can't hold the menu, and cap it to the room it has.
    useLayoutEffect(() => {
        if (!open) return;
        const place = () => {
            const a = anchor.current?.getBoundingClientRect();
            const el = ref.current;
            if (!a || !el) return;
            const above = a.top - EDGE_GAP;
            const below = window.innerHeight - a.bottom - EDGE_GAP;
            const preferred = placement === 'above' ? above : below;
            const other = placement === 'above' ? below : above;
            const flip = el.scrollHeight > preferred && other > preferred;
            setSide(flip ? (placement === 'above' ? 'below' : 'above') : placement);
            setRoom(Math.max(0, Math.floor(flip ? other : preferred)));
            // Slide sideways so a wide panel anchored near one edge stays inside the window.
            // Measure the layout box, which ignores transforms: the painted one can still carry an
            // old shift mid-transition, even after the inline transform is cleared.
            const parent = el.offsetParent;
            // The panel is position: absolute (Popover.css), so offsetParent is null only while it is closed or hidden.
            if (!parent) return;
            const naturalLeft = parent.getBoundingClientRect().left + parent.clientLeft - parent.scrollLeft + el.offsetLeft;
            // Keep the box inside the window gutter; one too wide for it keeps its inline start.
            const width = el.offsetWidth;
            const usable = window.innerWidth - 2 * EDGE_GAP;
            const rtl = getComputedStyle(el).direction === 'rtl';
            const left = width > usable
                ? (rtl ? window.innerWidth - EDGE_GAP - width : EDGE_GAP)
                : Math.min(Math.max(naturalLeft, EDGE_GAP), window.innerWidth - EDGE_GAP - width);
            setShift(left - naturalLeft);
        };
        place();
        // Content arrives after open (e.g. the model list), so re-measure when it changes.
        const observer = new MutationObserver(place);
        if (ref.current) observer.observe(ref.current, { childList: true, subtree: true });
        // Sizes can change with no DOM edit (a font arriving), and the box it hangs from moves it.
        const sized = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place);
        for (const target of [ref.current, anchor.current, ref.current?.offsetParent]) {
            if (target) sized?.observe(target);
        }
        window.addEventListener('resize', place);
        return () => {
            observer.disconnect();
            sized?.disconnect();
            window.removeEventListener('resize', place);
        };
    }, [open, placement, anchor]);

    useEscapeLayer(open);
    useEffect(() => {
        if (!open) return;
        const first = ref.current?.querySelector<HTMLElement>('button:not(:disabled), input, [tabindex="0"]');
        // A tall popover opens at its top, even when its first button sits at the bottom.
        first?.focus({ preventScroll: true });
        const onDown = (e: MouseEvent) => {
            const t = e.target as Node;
            if (ref.current?.contains(t) || anchor.current?.contains(t)) return;
            onClose();
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
            e.preventDefault();
            onClose();
            anchor.current?.focus();
        };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey, true);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey, true);
        };
    }, [open, onClose, anchor]);

    if (!open) return null;
    return (
        <div
            ref={ref}
            className={`popover popover-${side} popover-${align}${className ? ` ${className}` : ''}`}
            style={{
                ...(room === null ? {} : { '--popover-room': `${room}px` }),
                ...(shift ? { transform: `translateX(${shift}px)` } : {}),
            } as CSSProperties}
            role="dialog"
            aria-label={label}
        >
            {children}
        </div>
    );
}
