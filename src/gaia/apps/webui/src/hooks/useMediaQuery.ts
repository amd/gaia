// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

/** Whether *query* matches now, kept current as the window changes. */
export function useMediaQuery(query: string): boolean {
    const read = () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches;
    const [matches, setMatches] = useState(read);
    useEffect(() => {
        if (typeof window.matchMedia !== 'function') return;
        const mq = window.matchMedia(query);
        const sync = () => setMatches(mq.matches);
        sync();
        mq.addEventListener('change', sync);
        return () => mq.removeEventListener('change', sync);
    }, [query]);
    return matches;
}
