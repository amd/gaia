// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';
import './InfoBubble.css';

const GAP = 8;
const EDGE = 8;

/**
 * An info icon whose details open in a bubble on hover or keyboard focus.
 * The bubble renders on document.body so a scrolling popover can't clip it.
 */
export function InfoBubble({ label, warning, children }: { label: string; warning?: boolean; children: ReactNode }) {
    const id = useId();
    const icon = useRef<HTMLButtonElement>(null);
    const bubble = useRef<HTMLDivElement>(null);
    const [open, setOpen] = useState(false);
    const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

    useLayoutEffect(() => {
        if (!open) {
            setPos(null);
            return;
        }
        // Beside the menu the icon sits in, so the bubble never covers the list it describes.
        const a = icon.current?.getBoundingClientRect();
        const b = bubble.current?.getBoundingClientRect();
        const side = icon.current?.closest('[role="dialog"]')?.getBoundingClientRect() ?? a;
        if (!a || !b || !side) return;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let left: number;
        let top: number;
        if (side.right + GAP + b.width <= vw - EDGE) {
            left = side.right + GAP;
            top = a.top + a.height / 2 - b.height / 2;
        } else if (side.left - GAP - b.width >= EDGE) {
            left = side.left - GAP - b.width;
            top = a.top + a.height / 2 - b.height / 2;
        } else {
            // No room beside the menu (a narrow window): above the icon, or below it.
            left = Math.min(Math.max(EDGE, a.right - b.width), vw - EDGE - b.width);
            top = a.top - GAP - b.height >= EDGE ? a.top - GAP - b.height : a.bottom + GAP;
        }
        top = Math.min(Math.max(EDGE, top), vh - EDGE - b.height);
        setPos({ left, top });
    }, [open]);

    useEffect(() => {
        if (!open) return;
        // Placed once on open; a scroll would leave it beside the wrong row.
        const close = () => setOpen(false);
        window.addEventListener('scroll', close, true);
        return () => window.removeEventListener('scroll', close, true);
    }, [open]);

    return (
        <>
            <button
                ref={icon}
                type="button"
                className={`info-bubble-icon${warning ? ' is-warning' : ''}`}
                aria-label={label}
                aria-describedby={open ? id : undefined}
                onMouseEnter={() => setOpen(true)}
                onMouseLeave={() => setOpen(false)}
                onFocus={() => setOpen(true)}
                onBlur={() => setOpen(false)}
                onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
                onKeyDown={(e) => {
                    if (e.key === 'Escape' && open) {
                        e.stopPropagation();
                        setOpen(false);
                    }
                }}
            >
                <Info size={13} aria-hidden="true" />
            </button>
            {open && createPortal(
                <div
                    ref={bubble}
                    id={id}
                    role="tooltip"
                    className="info-bubble"
                    style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: 'hidden' }}
                >
                    {children}
                </div>,
                document.body,
            )}
        </>
    );
}
