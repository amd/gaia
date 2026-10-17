// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Loader2, X } from 'lucide-react';
import { RenderedContent } from './MessageBubble';
import { shortModelName } from '../stores/modelStore';
import type { Message, RetryResult } from '../types';
import './AnswerComparison.css';

export type ComparisonState =
    | { status: 'running'; model: string }
    | { status: 'failed'; model: string; error: string }
    | { status: 'ready'; result: RetryResult };

interface AnswerComparisonProps {
    original: Message;
    originalModel?: string | null;
    originalLatencyMs?: number;
    state: ComparisonState;
    /** Which answer is being kept right now, while that request runs. */
    keeping?: 'original' | 'retry' | null;
    onKeep: (which: 'original' | 'retry') => void;
    onClose: () => void;
}

function seconds(ms?: number): string | null {
    return ms != null && ms > 0 ? `${(ms / 1000).toFixed(1)} s` : null;
}

function Meta({ parts }: { parts: (string | null | undefined)[] }) {
    const shown = parts.filter(Boolean);
    return shown.length ? <div className="compare-meta">{shown.join(' · ')}</div> : null;
}

/** The original answer and another model's, side by side; keeping one continues the chat from it. */
export function AnswerComparison({ original, originalModel, originalLatencyMs, state, keeping, onKeep, onClose }: AnswerComparisonProps) {
    const retryModel = state.status === 'ready' ? state.result.model : state.model;
    const tps = original.stats?.tokens_per_second;
    return (
        <section className="answer-compare" aria-label="Compare answers">
            <header className="answer-compare-head">
                <span>Compare answers</span>
                <button type="button" className="msg-action-btn" onClick={onClose} aria-label="Close comparison" disabled={!!keeping}>
                    <X size={12} />
                </button>
            </header>
            <div className="answer-compare-cols">
                <article className="compare-col" aria-label={`Answer from ${originalModel ?? 'the original model'}`}>
                    <h4>{originalModel ? shortModelName(originalModel) : 'Original answer'}</h4>
                    <Meta parts={[seconds(originalLatencyMs), tps ? `${tps} tok/s` : null]} />
                    <div className="compare-body"><RenderedContent content={original.content} /></div>
                    <button
                        type="button"
                        className="compare-keep"
                        onClick={() => onKeep('original')}
                        disabled={!!keeping || state.status === 'running'}
                    >
                        {keeping === 'original' ? 'Keeping…' : 'Keep this answer'}
                    </button>
                </article>
                <article className="compare-col" aria-label={`Answer from ${retryModel}`}>
                    <h4>{shortModelName(retryModel)}</h4>
                    {state.status === 'running' && (
                        <div className="compare-wait">
                            <Loader2 size={14} className="spin" aria-hidden="true" />
                            <span>Loading {shortModelName(retryModel)}, then answering from the same evidence. Tools are not run again.</span>
                        </div>
                    )}
                    {state.status === 'failed' && <div className="compare-error" role="alert">{state.error}</div>}
                    {state.status === 'ready' && (
                        <>
                            <Meta
                                parts={[
                                    `${state.result.stats.seconds.toFixed(1)} s`,
                                    state.result.stats.load_seconds >= 1 ? `incl. ${state.result.stats.load_seconds.toFixed(1)} s loading` : null,
                                    state.result.stats.tokens_per_second ? `${state.result.stats.tokens_per_second} tok/s` : null,
                                ]}
                            />
                            <div className="compare-body"><RenderedContent content={state.result.content} /></div>
                            <button type="button" className="compare-keep" onClick={() => onKeep('retry')} disabled={!!keeping}>
                                {keeping === 'retry' ? 'Keeping…' : 'Keep this answer'}
                            </button>
                        </>
                    )}
                </article>
            </div>
        </section>
    );
}
