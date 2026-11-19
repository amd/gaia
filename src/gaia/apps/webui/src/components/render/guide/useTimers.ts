// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export type TimerStatus = 'idle' | 'running' | 'paused' | 'done';

export interface TimerView {
    status: TimerStatus;
    remainingSeconds: number;
}

interface Entry {
    status: TimerStatus;
    totalMs: number;
    /** Wall-clock end while running, so a hidden tab or a re-render never drifts the count. */
    endsAt: number | null;
    /** Left on the clock while paused. */
    leftMs: number;
}

export interface Timers {
    read(id: string, seconds: number): TimerView;
    /** Start, pause, or resume; a finished timer starts over. */
    toggle(id: string, seconds: number): void;
    reset(id: string): void;
}

const TICK_MS = 250;

let audio: AudioContext | null = null;

/** Created on the click that starts a timer, which is what lets it play later. */
function primeAudio(): void {
    if (audio || typeof window === 'undefined' || typeof window.AudioContext !== 'function') return;
    audio = new window.AudioContext();
}

function chime(): void {
    if (!audio) return;
    const start = audio.currentTime;
    [0, 0.35, 0.7].forEach((offset) => {
        const osc = audio!.createOscillator();
        const gain = audio!.createGain();
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, start + offset);
        gain.gain.exponentialRampToValueAtTime(0.25, start + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + 0.3);
        osc.connect(gain).connect(audio!.destination);
        osc.start(start + offset);
        osc.stop(start + offset + 0.32);
    });
}

/** One timer store per card, shared by the inline steps and the guided view. */
export function useTimers(): Timers {
    const [entries, setEntries] = useState<Record<string, Entry>>({});
    const [now, setNow] = useState(() => Date.now());
    const entriesRef = useRef(entries);
    useEffect(() => { entriesRef.current = entries; }, [entries]);

    const running = Object.values(entries).some((e) => e.status === 'running');
    useEffect(() => {
        if (!running) return;
        const id = setInterval(() => {
            const t = Date.now();
            setNow(t);
            const finished = Object.entries(entriesRef.current)
                .filter(([, e]) => e.status === 'running' && e.endsAt !== null && e.endsAt <= t);
            if (!finished.length) return;
            setEntries((prev) => {
                const next = { ...prev };
                for (const [key] of finished) next[key] = { ...next[key], status: 'done', endsAt: null, leftMs: 0 };
                return next;
            });
            chime();
        }, TICK_MS);
        return () => clearInterval(id);
    }, [running]);

    const toggle = useCallback((id: string, seconds: number) => {
        primeAudio();
        const t = Date.now();
        setNow(t);
        setEntries((prev) => {
            const e = prev[id];
            const totalMs = seconds * 1000;
            if (!e || e.status === 'idle' || e.status === 'done') {
                return { ...prev, [id]: { status: 'running', totalMs, endsAt: t + totalMs, leftMs: totalMs } };
            }
            if (e.status === 'running') {
                return { ...prev, [id]: { ...e, status: 'paused', endsAt: null, leftMs: Math.max(0, e.endsAt! - t) } };
            }
            return { ...prev, [id]: { ...e, status: 'running', endsAt: t + e.leftMs } };
        });
    }, []);

    const reset = useCallback((id: string) => {
        setEntries((prev) => {
            if (!prev[id]) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
        });
    }, []);

    const read = useCallback((id: string, seconds: number): TimerView => {
        const e = entries[id];
        if (!e) return { status: 'idle', remainingSeconds: seconds };
        const leftMs = e.status === 'running' && e.endsAt !== null ? Math.max(0, e.endsAt - now) : e.leftMs;
        return { status: e.status, remainingSeconds: leftMs / 1000 };
    }, [entries, now]);

    return useMemo(() => ({ read, toggle, reset }), [read, toggle, reset]);
}
