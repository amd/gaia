// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';

/** While streaming, the text lags by at most `intervalMs`, so a fast stream parses a few times a second. */
export function useThrottledText(text: string, streaming: boolean, intervalMs: number): string {
    const [shown, setShown] = useState(text);
    const lastAt = useRef(0);
    useEffect(() => {
        if (!streaming) return;
        const wait = Math.max(0, lastAt.current + intervalMs - Date.now());
        const timer = setTimeout(() => {
            lastAt.current = Date.now();
            setShown(text);
        }, wait);
        return () => clearTimeout(timer);
    }, [text, streaming, intervalMs]);
    return streaming ? shown : text;
}
