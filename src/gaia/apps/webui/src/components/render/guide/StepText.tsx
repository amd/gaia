// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Fragment } from 'react';
import { Check, Pause, Play } from 'lucide-react';
import { findDurations, formatClock } from './durations';
import type { Timers } from './useTimers';

export function timerId(stepIndex: number, spanIndex: number): string {
    return `${stepIndex}:${spanIndex}`;
}

export function TimerChip({ id, seconds, timers, label }: {
    id: string;
    seconds: number;
    timers: Timers;
    label: string;
}) {
    const view = timers.read(id, seconds);
    const action = view.status === 'running' ? 'Pause' : view.status === 'paused' ? 'Resume' : 'Start';
    const Icon = view.status === 'running' ? Pause : view.status === 'done' ? Check : Play;
    return (
        <button
            type="button"
            className={`render-guide-chip is-${view.status}`}
            onClick={() => timers.toggle(id, seconds)}
            aria-label={`${action} ${label} timer`}
        >
            <Icon size={11} aria-hidden="true" />
            <span className="render-guide-chip__clock">
                {view.status === 'done' ? 'Done' : formatClock(view.remainingSeconds)}
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
    const spans = findDurations(text);
    let cursor = 0;
    return (
        <>
            {spans.map((span, i) => {
                const before = text.slice(cursor, span.end);
                const phrase = text.slice(span.start, span.end);
                cursor = span.end;
                return (
                    <Fragment key={i}>
                        {before}
                        {' '}
                        <TimerChip id={timerId(stepIndex, i)} seconds={span.seconds} timers={timers} label={phrase} />
                    </Fragment>
                );
            })}
            {text.slice(cursor)}
        </>
    );
}
