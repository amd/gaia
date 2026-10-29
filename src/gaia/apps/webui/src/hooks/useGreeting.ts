// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import type { GreetingEvent } from '../types';

export type Greeting =
    | { kind: 'loading'; phase: string; model: string | null; device: 'npu' | 'gpu' | 'cpu' | null; seconds: number }
    | { kind: 'writing'; text: string }
    | { kind: 'ready'; text: string }
    | { kind: 'failed'; message: string };

const TICK_MS = 1_000;

/** The finished greeting of the empty chat on screen, kept until a message is sent from it. */
let kept: string | null = null;

/** Forget the kept greeting: its chat was started, so the next empty chat gets its own. */
export function forgetGreeting(): void {
    kept = null;
}

/**
 * The agent's greeting for this empty chat, and how far along it is.
 *
 * Asked for once per empty chat: a finished greeting is reused until
 * `forgetGreeting`, so re-renders and coming back to the chat don't ask again.
 */
export function useGreeting(): { greeting: Greeting; retry: () => void } {
    const [greeting, setGreeting] = useState<Greeting>(() =>
        kept !== null ? { kind: 'ready', text: kept } : { kind: 'loading', phase: 'starting', model: null, device: null, seconds: 0 });
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        if (kept !== null) return;
        const controller = new AbortController();
        let text = '';
        setGreeting({ kind: 'loading', phase: 'starting', model: null, device: null, seconds: 0 });
        const tick = setInterval(() => {
            setGreeting((g) => (g.kind === 'loading' ? { ...g, seconds: g.seconds + 1 } : g));
        }, TICK_MS);
        const onEvent = (event: GreetingEvent) => {
            if (controller.signal.aborted) return;
            if (event.type === 'status') {
                setGreeting((g) => (g.kind === 'loading'
                    ? { ...g, phase: event.phase, model: event.model ?? null, device: event.device ?? null }
                    : g));
            } else if (event.type === 'chunk') {
                text += event.content;
                setGreeting({ kind: 'writing', text });
            } else if (event.type === 'done') {
                clearInterval(tick);
                kept = event.text;
                setGreeting({ kind: 'ready', text: event.text });
            } else {
                clearInterval(tick);
                setGreeting({ kind: 'failed', message: event.message });
            }
        };
        void api.streamGreeting(onEvent, controller.signal);
        return () => {
            controller.abort();
            clearInterval(tick);
        };
    }, [attempt]);

    const retry = useCallback(() => setAttempt((n) => n + 1), []);
    return { greeting, retry };
}
