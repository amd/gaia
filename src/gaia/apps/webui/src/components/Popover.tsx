// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, type ReactNode } from 'react';
import './Popover.css';

interface PopoverProps {
    open: boolean;
    onClose: () => void;
    /** The button that toggles it; kept out of the outside-click check. */
    anchor: React.RefObject<HTMLElement | null>;
    label: string;
    placement?: 'above' | 'below';
    align?: 'start' | 'end';
    children: ReactNode;
}

/** A small anchored panel that closes on Escape or an outside click, and returns focus. */
export function Popover({ open, onClose, anchor, label, placement = 'above', align = 'start', children }: PopoverProps) {
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const first = ref.current?.querySelector<HTMLElement>('button:not(:disabled), input, [tabindex="0"]');
        first?.focus();
        const onDown = (e: MouseEvent) => {
            const t = e.target as Node;
            if (ref.current?.contains(t) || anchor.current?.contains(t)) return;
            onClose();
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== 'Escape') return;
            e.stopPropagation();
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
        <div ref={ref} className={`popover popover-${placement} popover-${align}`} role="dialog" aria-label={label}>
            {children}
        </div>
    );
}
