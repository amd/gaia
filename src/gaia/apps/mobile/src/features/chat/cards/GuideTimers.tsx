// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { formatClock, splitAtDurations } from '@webui/components/render/guide/durations';
import {
    TICK_MS,
    finishedTimers,
    hasRunning,
    markDone,
    readTimer,
    resetTimer,
    toggleTimer,
    type TimerEntries,
    type Timers,
} from '@webui/components/render/guide/timerState';

import { useT } from '@/i18n/useT';
import { Icon, Ltr, Pressable, Row, Surface, Text } from '@/ui';
import { Check, Pause, Play } from '@/ui/icons';

export const timerId = (stepIndex: number, spanIndex: number): string => `${stepIndex}:${spanIndex}`;

/** Timers run on the phone while the app is open; none survive a closed app, and none notify. */
export function useGuideTimers(): Timers {
    const [entries, setEntries] = useState<TimerEntries>({});
    const [now, setNow] = useState(() => Date.now());
    const entriesRef = useRef(entries);
    useEffect(() => {
        entriesRef.current = entries;
    }, [entries]);

    const running = hasRunning(entries);
    useEffect(() => {
        if (!running) return;
        const id = setInterval(() => {
            const t = Date.now();
            setNow(t);
            const finished = finishedTimers(entriesRef.current, t);
            if (!finished.length) return;
            setEntries((prev) => markDone(prev, finished));
            buzz();
        }, TICK_MS);
        return () => clearInterval(id);
    }, [running]);

    const toggle = useCallback((id: string, seconds: number) => {
        const t = Date.now();
        setNow(t);
        setEntries((prev) => toggleTimer(prev, id, seconds, t));
    }, []);
    const reset = useCallback((id: string) => setEntries((prev) => resetTimer(prev, id)), []);
    const read = useCallback((id: string, seconds: number) => readTimer(entries, id, seconds, now), [entries, now]);
    return useMemo(() => ({ read, toggle, reset }), [read, toggle, reset]);
}

function buzz(): void {
    // Loaded on use: the web build of expo-haptics reads window.matchMedia as it loads.
    import('expo-haptics')
        .then((Haptics) => Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success))
        .catch((err: unknown) => console.warn('[guide] could not buzz when a timer finished', err));
}

export function TimerChip({ id, seconds, timers, phrase, showPhrase }: { id: string; seconds: number; timers: Timers; phrase: string; showPhrase: boolean }) {
    const t = useT();
    const view = timers.read(id, seconds);
    const action = view.status === 'running' ? 'cards.timerPause' : view.status === 'paused' ? 'cards.timerResume' : 'cards.timerStart';
    const done = view.status === 'done';
    const tone = done ? 'success' : view.status === 'running' ? 'accent' : 'primary';
    return (
        <Pressable onPress={() => timers.toggle(id, seconds)} accessibilityLabel={t(action, { label: phrase })} testID={`guide-timer-${id}`}>
            <Surface tone={view.status === 'running' ? 'userMsg' : 'card'} radius="full" border padding="sm">
                <Row gap="xs">
                    <Icon icon={view.status === 'running' ? Pause : done ? Check : Play} size="sm" tone={tone} />
                    {showPhrase ? (
                        <Text role="caption" tone="secondary" dir="auto" numberOfLines={1}>
                            {phrase}
                        </Text>
                    ) : null}
                    <Text role="label" weight="medium" mono tone={tone}>
                        {done ? t.bare('cards.done') : <Ltr>{formatClock(view.remainingSeconds)}</Ltr>}
                    </Text>
                </Row>
            </Surface>
        </Pressable>
    );
}

/** Timer chips for each duration in a step's text, below the text so each one is a full-size tap target. */
export function StepTimers({ text, stepIndex, timers }: { text: string; stepIndex: number; timers: Timers }) {
    const { segments } = splitAtDurations(text);
    if (segments.length === 0) return null;
    return (
        <Row gap="sm" wrap>
            {segments.map((segment, i) => (
                <TimerChip key={i} id={timerId(stepIndex, i)} seconds={segment.seconds} timers={timers} phrase={segment.phrase} showPhrase={segments.length > 1} />
            ))}
        </Row>
    );
}
