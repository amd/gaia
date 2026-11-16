// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { DownloadMeter, type Estimate } from '@webui/components/setup/format';
import type { SetupModelProgress, SetupOverall } from '@webui/types';
import { useEffect, useRef, useState } from 'react';

/** Speed and time left per row and overall, from the byte counts each poll brings; the web UI's DownloadProgress, as a hook. */
export function useDownloadEstimates(rows: SetupModelProgress[], overall: SetupOverall | undefined, checking: boolean) {
    const meters = useRef(new Map<string, DownloadMeter>());
    const resumedFrom = useRef(new Map<string, number | null>());
    const overallMeter = useRef(new DownloadMeter());
    const wasMoving = useRef(false);
    const checkStarted = useRef(new Map<string, number>());
    const [now, setNow] = useState(() => Date.now());

    // A countdown (keep trying) and a check's elapsed time move between polls.
    const ticking = checking || rows.some((m) => m.retrying_until);
    useEffect(() => {
        if (!ticking) return undefined;
        const tick = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(tick);
    }, [ticking]);

    const [estimates, setEstimates] = useState<{ rows: Record<string, Estimate>; overall: Estimate | undefined; checks: Record<string, number> }>({ rows: {}, overall: undefined, checks: {} });
    useEffect(() => {
        const at = Date.now();
        const out: Record<string, Estimate> = {};
        for (const m of rows) {
            if (m.state !== 'downloading') {
                meters.current.delete(m.id);
                continue;
            }
            let meter = meters.current.get(m.id);
            const before = m.downloaded_before_gb ?? null;
            if (!meter || resumedFrom.current.get(m.id) !== before) {
                meter = new DownloadMeter();
                meters.current.set(m.id, meter);
                resumedFrom.current.set(m.id, before);
            }
            out[m.id] = meter.update(m.bytes_done, m.bytes_total, at);
        }
        const moving = rows.some((m) => m.state === 'downloading');
        // A resume or retry after a pause starts its speed and time left fresh.
        if (moving && !wasMoving.current) overallMeter.current = new DownloadMeter();
        wasMoving.current = moving;
        // Time left counts only rows still coming: a failed or cancelled one isn't.
        const coming = rows
            .filter((m) => m.state === 'downloading' || m.state === 'queued')
            .reduce((sum, m) => sum + Math.max(0, m.bytes_total - m.bytes_done), 0);
        const total = moving && overall ? overallMeter.current.update(overall.bytes_done, overall.bytes_done + coming, at) : undefined;
        // Seconds each row has been checked, from when it first showed as checking.
        const checks: Record<string, number> = {};
        for (const m of rows) {
            if (checking && m.state === 'verifying') {
                const started = checkStarted.current.get(m.id) ?? at;
                checkStarted.current.set(m.id, started);
                checks[m.id] = Math.floor((at - started) / 1000);
            } else checkStarted.current.delete(m.id);
        }
        setEstimates({ rows: out, overall: total, checks });
    }, [rows, overall, checking, now]);

    const checkingSeconds = (id: string): number => estimates.checks[id] ?? 0;

    const { rows: rowEstimates, overall: overallEstimate } = estimates;
    return { estimates: rowEstimates, overallEstimate, now, checkingSeconds };
}
