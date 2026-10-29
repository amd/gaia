// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, Pause, Play, Square, X } from 'lucide-react';
import { useEscapeToClose } from '../../../hooks/useEscapeToClose';
import { useFocusTrap } from '../../../hooks/useFocusTrap';
import { findDurations, formatClock, parseSets } from './durations';
import { itemDisplay, stepText } from './guideText';
import type { GuideViewState } from './guideText';
import type { GuideDoc } from './guideDoc';
import { StepText, timerId } from './StepText';
import type { Timers } from './useTimers';

/** Keeps the screen on while the guide is open; false when the platform refused or can't. */
function useWakeLock(): boolean {
    const [held, setHeld] = useState(true);
    useEffect(() => {
        if (!('wakeLock' in navigator)) {
            setHeld(false);
            return;
        }
        let sentinel: WakeLockSentinel | null = null;
        let live = true;
        const request = () => {
            if (document.visibilityState !== 'visible') return;
            navigator.wakeLock.request('screen').then(
                (s) => {
                    if (!live) { void s.release(); return; }
                    sentinel = s;
                    setHeld(true);
                },
                () => { if (live) setHeld(false); },
            );
        };
        request();
        // The browser drops the lock whenever the page is hidden.
        document.addEventListener('visibilitychange', request);
        return () => {
            live = false;
            document.removeEventListener('visibilitychange', request);
            void sentinel?.release();
        };
    }, []);
    return held;
}

export function GuidedView({ doc, state, timers, onClose }: {
    doc: GuideDoc;
    state: GuideViewState;
    timers: Timers;
    onClose: () => void;
}) {
    const [index, setIndex] = useState(0);
    const [setsDone, setSetsDone] = useState<Record<number, number>>({});
    const dialogRef = useRef<HTMLDivElement>(null);
    const wakeLock = useWakeLock();
    const total = doc.steps.length;
    const last = index === total - 1;

    useEscapeToClose(true, onClose);
    useFocusTrap(dialogRef, true);

    useEffect(() => {
        const opener = document.activeElement as HTMLElement | null;
        dialogRef.current?.focus();
        return () => opener?.focus();
    }, []);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
            if (e.target instanceof HTMLElement && e.target.closest('input, textarea, select')) return;
            if (e.key === 'ArrowRight') setIndex((i) => Math.min(total - 1, i + 1));
            else if (e.key === 'ArrowLeft') setIndex((i) => Math.max(0, i - 1));
            else return;
            e.preventDefault();
        };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [total]);

    const step = doc.steps[index];
    const body = stepText(doc, step.body, state.system);
    const spans = doc.features.timers ? findDurations(body) : [];
    const sets = doc.type === 'workout' ? parseSets(body) : null;
    const done = setsDone[index] ?? 0;

    const restIndex = sets?.restIndex ?? null;
    const resting = restIndex !== null && timers.read(timerId(index, restIndex), spans[restIndex].seconds).status !== 'idle';
    const primary = resting ? restIndex : spans.findIndex((_, i) => i !== restIndex);
    const bigIndex = primary === -1 ? (spans.length ? 0 : null) : primary;
    const big = bigIndex === null ? null : { id: timerId(index, bigIndex), seconds: spans[bigIndex].seconds };
    const bigView = big ? timers.read(big.id, big.seconds) : null;

    const finishSet = () => {
        if (!sets?.sets) return;
        setSetsDone((prev) => ({ ...prev, [index]: Math.min(sets.sets!, done + 1) }));
        if (restIndex !== null && done + 1 < sets.sets) {
            const id = timerId(index, restIndex);
            timers.reset(id);
            timers.toggle(id, spans[restIndex].seconds);
        }
    };

    return createPortal(
        <div ref={dialogRef} className="render-guided" role="dialog" aria-modal="true" aria-label={doc.title} tabIndex={-1}>
            <div className="render-guided__top">
                <div className="render-guided__heading">
                    <span className="render-guided__guide-title">{doc.title}</span>
                    {doc.items.length > 0 && (
                        <details className="render-guided__items">
                            <summary>{doc.features.itemsLabel}</summary>
                            <ul>
                                {doc.items.map((_, i) => {
                                    const { qty, text } = itemDisplay(doc, i, state);
                                    return (
                                        <li key={i}>
                                            {qty && <strong>{qty}</strong>} {text}
                                        </li>
                                    );
                                })}
                            </ul>
                        </details>
                    )}
                </div>
                <button type="button" className="render-guided__icon" onClick={onClose} aria-label="Close guided view">
                    <X size={22} />
                </button>
            </div>

            <div className="render-guided__stage">
                <h2 className="render-guided__step-title">{step.title ?? `Step ${index + 1}`}</h2>
                <p className="render-guided__step-text">
                    <StepText text={body} stepIndex={index} timers={timers} withTimers={doc.features.timers} />
                </p>

                {sets?.sets && (
                    <div className="render-guided__sets">
                        <span>Set {Math.min(done + 1, sets.sets)} of {sets.sets}{done >= sets.sets ? ', all done' : ''}</span>
                        <button type="button" className="render-guided__button" onClick={finishSet} disabled={done >= sets.sets}>
                            Set done
                        </button>
                    </div>
                )}

                {big && bigView && (
                    <div className={`render-guided__timer is-${bigView.status}`}>
                        <span className="render-guided__clock" role="timer">
                            {bigView.status === 'done' ? 'Done' : formatClock(bigView.remainingSeconds)}
                        </span>
                        <div className="render-guided__timer-actions">
                            <button type="button" className="render-guided__button is-primary" onClick={() => timers.toggle(big.id, big.seconds)}>
                                {bigView.status === 'running' ? <Pause size={16} /> : <Play size={16} />}
                                {bigView.status === 'running' ? 'Pause' : bigView.status === 'paused' ? 'Resume' : 'Start'}
                            </button>
                            {bigView.status !== 'idle' && (
                                <button type="button" className="render-guided__button" onClick={() => timers.reset(big.id)}>
                                    <Square size={14} /> Stop
                                </button>
                            )}
                        </div>
                    </div>
                )}
            </div>

            <div className="render-guided__nav">
                <button
                    type="button"
                    className="render-guided__icon"
                    onClick={() => setIndex((i) => Math.max(0, i - 1))}
                    disabled={index === 0}
                    aria-label="Previous step"
                >
                    <ChevronLeft size={26} />
                </button>
                <div className="render-guided__progress">
                    <span aria-live="polite">Step {index + 1} of {total}</span>
                    {!wakeLock && <span className="render-guided__note">Screen may sleep</span>}
                </div>
                {last ? (
                    <button type="button" className="render-guided__button is-primary" onClick={onClose}>
                        Finish
                    </button>
                ) : (
                    <button
                        type="button"
                        className="render-guided__icon"
                        onClick={() => setIndex((i) => Math.min(total - 1, i + 1))}
                        aria-label="Next step"
                    >
                        <ChevronRight size={26} />
                    </button>
                )}
            </div>
        </div>,
        document.body,
    );
}
