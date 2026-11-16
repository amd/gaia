// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment } from 'react';
import { Check, Pause, Play } from 'lucide-react';
import { formatClock, splitAtDurations } from './durations';
import type { Timers } from './useTimers';
import { useT } from '../../../i18n/useT';

export function timerId(stepIndex: number, spanIndex: number): string {
    return `${stepIndex}:${spanIndex}`;
}

export function TimerChip({ id, seconds, timers, label }: {
    id: string;
    seconds: number;
    timers: Timers;
    label: string;
}) {
    const t = useT();
    const view = timers.read(id, seconds);
    const action = view.status === 'running' ? 'cards.timerPause' : view.status === 'paused' ? 'cards.timerResume' : 'cards.timerStart';
    const Icon = view.status === 'running' ? Pause : view.status === 'done' ? Check : Play;
    return (
        <button
            type="button"
            className={`render-guide-chip is-${view.status}`}
            onClick={() => timers.toggle(id, seconds)}
            aria-label={t(action, { label })}
        >
            <Icon size={11} aria-hidden="true" />
            <span className="render-guide-chip__clock">
                {view.status === 'done' ? t('cards.done') : formatClock(view.remainingSeconds)}
            </span>
        </button>
    );
}

/** Step text with a timer chip after every duration phrase. Rendered as text nodes only. */
export function StepText({ text, stepIndex, timers, withTimers }: {
    text: string;
    stepIndex: number;
    timers: Timers;
    withTimers: boolean;
}) {
    if (!withTimers) return <>{text}</>;
    const { segments, tail } = splitAtDurations(text);
    return (
        <>
            {segments.map((segment, i) => (
                <Fragment key={i}>
                    {segment.before}
                    {' '}
                    <TimerChip id={timerId(stepIndex, i)} seconds={segment.seconds} timers={timers} label={segment.phrase} />
                </Fragment>
            ))}
            {tail}
        </>
    );
}
