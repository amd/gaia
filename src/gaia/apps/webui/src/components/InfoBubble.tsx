// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
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
        const a = icon.current?.getBoundingClientRect();
        const b = bubble.current?.getBoundingClientRect();
        if (!a || !b) return;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let left: number;
        let top: number;
        if (a.right + GAP + b.width <= vw - EDGE) {
            left = a.right + GAP;
            top = a.top + a.height / 2 - b.height / 2;
        } else if (a.left - GAP - b.width >= EDGE) {
            left = a.left - GAP - b.width;
            top = a.top + a.height / 2 - b.height / 2;
        } else {
            // No room beside the icon (a narrow window): above it, or below when that fits better.
            left = Math.min(Math.max(EDGE, a.right - b.width), vw - EDGE - b.width);
            top = a.top - GAP - b.height >= EDGE ? a.top - GAP - b.height : a.bottom + GAP;
        }
        top = Math.min(Math.max(EDGE, top), vh - EDGE - b.height);
        setPos({ left, top });
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
