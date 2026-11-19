// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Loader2, X } from 'lucide-react';
import { ref } from '../i18n/registry';
import { useT, type Translate } from '../i18n/useT';
import { MarkedText, markLtr } from './MarkedText';
import { RenderedContent } from './MessageBubble';
import { displayModelName, useModelNames } from '../stores/modelStore';
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

function seconds(t: Translate, secs: number): string {
    return markLtr(t('answer.duration.sec', { n: secs.toFixed(1) }));
}

function rate(t: Translate, tps?: number | null): string | null {
    return tps ? markLtr(t('compare.rate', { n: tps })) : null;
}

/** Each part keeps its number and unit left to right, so an RTL line can't swap them. */
function Meta({ parts }: { parts: (string | null | undefined)[] }) {
    const shown = parts.filter((p): p is string => !!p);
    if (!shown.length) return null;
    return (
        <div className="compare-meta">
            {shown.map((part, i) => <span key={i}>{i > 0 && ' · '}<MarkedText text={part} /></span>)}
        </div>
    );
}

/** The original answer and another model's, side by side; keeping one continues the chat from it. */
export function AnswerComparison({ original, originalModel, originalLatencyMs, state, keeping, onKeep, onClose }: AnswerComparisonProps) {
    useModelNames();
    const t = useT();
    const retryModel = state.status === 'ready' ? state.result.model : state.model;
    const tps = original.stats?.tokens_per_second;
    return (
        <section className="answer-compare" aria-label={t('compare.title')}>
            <header className="answer-compare-head">
                <span>{t('compare.title')}</span>
                <button type="button" className="msg-action-btn" onClick={onClose} aria-label={t('compare.close')} disabled={!!keeping}>
                    <X size={12} />
                </button>
            </header>
            <div className="answer-compare-cols">
                <article className="compare-col" aria-label={t('compare.answerFrom', { model: originalModel ?? ref('compare.originalModel') })}>
                    <h4>{originalModel ? displayModelName(originalModel) : t('compare.original')}</h4>
                    <Meta parts={[originalLatencyMs != null && originalLatencyMs > 0 ? seconds(t, originalLatencyMs / 1000) : null, rate(t, tps)]} />
                    <div className="compare-body"><RenderedContent content={original.content} /></div>
                    <button
                        type="button"
                        className="compare-keep"
                        onClick={() => onKeep('original')}
                        disabled={!!keeping || state.status === 'running'}
                    >
                        {t(keeping === 'original' ? 'compare.keeping' : 'compare.keep')}
                    </button>
                </article>
                <article className="compare-col" aria-label={t('compare.answerFrom', { model: retryModel })}>
                    <h4>{displayModelName(retryModel)}</h4>
                    {state.status === 'running' && (
                        <div className="compare-wait">
                            <Loader2 size={14} className="spin" aria-hidden="true" />
                            <span>{t('compare.loading', { model: displayModelName(retryModel) })}</span>
                        </div>
                    )}
                    {state.status === 'failed' && <div className="compare-error" role="alert">{state.error}</div>}
                    {state.status === 'ready' && (
                        <>
                            <Meta
                                parts={[
                                    seconds(t, state.result.stats.seconds),
                                    state.result.stats.load_seconds >= 1
                                        ? t.compose((say) => say('compare.loadTime', {
                                            time: markLtr(say('answer.duration.sec', { n: state.result.stats.load_seconds.toFixed(1) })),
                                        }))
                                        : null,
                                    rate(t, state.result.stats.tokens_per_second),
                                ]}
                            />
                            <div className="compare-body"><RenderedContent content={state.result.content} /></div>
                            <button type="button" className="compare-keep" onClick={() => onKeep('retry')} disabled={!!keeping}>
                                {t(keeping === 'retry' ? 'compare.keeping' : 'compare.keep')}
                            </button>
                        </>
                    )}
                </article>
            </div>
        </section>
    );
}
