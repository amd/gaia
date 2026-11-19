// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

/**
 * A log that opens at its newest line and keeps following it as lines arrive,
 * until the reader scrolls up to read; scrolling back to the bottom follows again.
 */
export function FollowLog({ lines, className, id }: { lines: string[]; className: string; id?: string }) {
    const box = useRef<HTMLPreElement>(null);
    const pinned = useRef(true);
    const text = lines.join('\n');
    useEffect(() => {
        const el = box.current;
        if (el && pinned.current) el.scrollTop = el.scrollHeight;
    }, [text]);
    return (
        <pre
            ref={box}
            id={id}
            className={className}
            dir="ltr"
            tabIndex={0}
            onScroll={(e) => {
                const el = e.currentTarget;
                pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
            }}
        >
            {text}
        </pre>
    );
}
