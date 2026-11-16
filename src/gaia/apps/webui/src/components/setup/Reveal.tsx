// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState, type ReactNode } from 'react';

const CLOSE_MS = 180;

/**
 * Children that grow open and collapse shut (height and opacity, 180 ms).
 * They stay mounted only while closing, so a closed one is gone from the
 * accessibility tree. Reduced motion makes it instant through the global rule.
 */
export function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
    const [mounted, setMounted] = useState(open);
    const [expanded, setExpanded] = useState(open);
    const last = useRef(children);
    if (open) last.current = children;

    useEffect(() => {
        if (open) {
            setMounted(true);
            const frame = requestAnimationFrame(() => setExpanded(true));
            return () => cancelAnimationFrame(frame);
        }
        setExpanded(false);
        const t = setTimeout(() => setMounted(false), CLOSE_MS);
        return () => clearTimeout(t);
    }, [open]);

    if (!mounted) return null;
    return (
        <div className={`reveal${expanded ? ' is-open' : ''}`} aria-hidden={open ? undefined : true}>
            <div className="reveal-inner">{open ? children : last.current}</div>
        </div>
    );
}
