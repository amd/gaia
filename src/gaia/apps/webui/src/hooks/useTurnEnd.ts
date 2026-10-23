// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

/** Call *onEnd* each time *busy* goes from true to false: a reply finished. */
export function useTurnEnd(busy: boolean, onEnd: () => void): void {
    const was = useRef(busy);
    const cb = useRef(onEnd);
    cb.current = onEnd;
    useEffect(() => {
        const ended = was.current && !busy;
        was.current = busy;
        if (ended) cb.current();
    }, [busy]);
}
