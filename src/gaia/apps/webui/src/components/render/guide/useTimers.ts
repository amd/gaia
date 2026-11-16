// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
    TICK_MS,
    finishedTimers,
    hasRunning,
    markDone,
    readTimer,
    resetTimer,
    toggleTimer,
} from './timerState';
import type { TimerEntries, TimerView, Timers } from './timerState';

export type { TimerStatus, TimerView, Timers } from './timerState';

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
    const [entries, setEntries] = useState<TimerEntries>({});
    const [now, setNow] = useState(() => Date.now());
    const entriesRef = useRef(entries);
    useEffect(() => { entriesRef.current = entries; }, [entries]);

    const running = hasRunning(entries);
    useEffect(() => {
        if (!running) return;
        const id = setInterval(() => {
            const t = Date.now();
            setNow(t);
            const finished = finishedTimers(entriesRef.current, t);
            if (!finished.length) return;
            setEntries((prev) => markDone(prev, finished));
            chime();
        }, TICK_MS);
        return () => clearInterval(id);
    }, [running]);

    const toggle = useCallback((id: string, seconds: number) => {
        primeAudio();
        const t = Date.now();
        setNow(t);
        setEntries((prev) => toggleTimer(prev, id, seconds, t));
    }, []);

    const reset = useCallback((id: string) => setEntries((prev) => resetTimer(prev, id)), []);

    const read = useCallback((id: string, seconds: number): TimerView => readTimer(entries, id, seconds, now), [entries, now]);

    return useMemo(() => ({ read, toggle, reset }), [read, toggle, reset]);
}
