// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

/** The current moment, refreshed when the local date changes (at midnight, or on return from sleep). */
export function useToday(): Date {
    const [now, setNow] = useState(() => new Date());
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout>;
        const refresh = () => setNow((prev) => (dayKey(prev) === dayKey(new Date()) ? prev : new Date()));
        const arm = () => {
            const d = new Date();
            const next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
            timer = setTimeout(() => { refresh(); arm(); }, next - d.getTime() + 1000);
        };
        arm();
        document.addEventListener('visibilitychange', refresh);
        window.addEventListener('focus', refresh);
        return () => {
            clearTimeout(timer);
            document.removeEventListener('visibilitychange', refresh);
            window.removeEventListener('focus', refresh);
        };
    }, []);
    return now;
}
