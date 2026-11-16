// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { QuestionText } from './QuestionText';
import { UnsupportedCard } from './UnsupportedCard';
import { respondToInput } from '../../services/api';
import { useChatStore, type LiveQuestion } from '../../stores/chatStore';
import { tEn, useT } from '../../i18n/useT';
import { spokenDuration } from '../../utils/spokenDuration.core';

/**
 * NeedsInputCard — the answerable `needs_input` surface (#2595).
 *
 * Unlike `needs_confirmation` (stateless, informational, run already over),
 * this is a LIVE mid-run question: the agent is blocked server-side waiting
 * for an answer, and submitting one unblocks the same run. `session_id` and
 * `request_id` travel inside `data` (set by ChatView from the SSE event) so
 * this card can post the answer without any extra context plumbing.
 *
 * The chat's message box is where a typed answer goes (ChatView sends it here
 * through `answerQuestion`); the card offers the choices. A sensitive question
 * keeps its own masked field, so the secret never sits in the message box.
 *
 * Rendering safety: `question`/option labels can carry agent/tool-derived
 * text. Option labels are plain text nodes; the question goes through
 * `QuestionText`, which shows formatting only (no links, images or HTML).
 *
 * Once answered the card stays as a record of the question and the answer
 * (`chatStore.questionAnswers`); a sensitive answer is never echoed back.
 * Answerable only while the agent still waits: the question must be in
 * `chatStore.liveQuestions`. Once its turn ends or its wait runs out the card
 * says so and drops its buttons, and a click racing that is told plainly.
 */

interface InputOption {
    value: string;
    label: string;
    description: string;
}

interface NeedsInputPayload {
    session_id: string;
    request_id: string;
    question: string;
    options: InputOption[];
    allow_free_text: boolean;
    sensitive: boolean;
}

function isNeedsInputPayload(value: unknown): value is NeedsInputPayload {
    if (!value || typeof value !== 'object') return false;
    const v = value as Record<string, unknown>;
    return (
        typeof v.session_id === 'string' &&
        typeof v.request_id === 'string' &&
        typeof v.question === 'string' &&
        Array.isArray(v.options) &&
        typeof v.allow_free_text === 'boolean' &&
        typeof v.sensitive === 'boolean'
    );
}

type SubmitState =
    | { status: 'pending' }
    | { status: 'submitting' }
    | { status: 'error'; message: string };

export const QUESTION_EXPIRED_NOTE = tEn('question.expired');

const SENSITIVE_PLACEHOLDER = '••••••';

/** The backend no longer waits on this question; the card says so instead of an error. */
export class QuestionGoneError extends Error {}

/** A 404 `ApiError` from the answer endpoint: the backend no longer waits on this question.
 *  A 409 is a relay that could not deliver it, which may still succeed. */
function isGoneError(err: unknown): boolean {
    return (err as { httpStatus?: unknown } | null)?.httpStatus === 404;
}

/**
 * Post an answer and keep it as the card's record. `shown` is what the
 * transcript says was answered (an option's label); null keeps it hidden.
 * Throws `QuestionGoneError` when the agent stopped waiting, else the request's error.
 */
export async function answerQuestion(sessionId: string, requestId: string, value: string, shown: string | null): Promise<void> {
    const store = useChatStore.getState();
    try {
        await respondToInput(sessionId, requestId, value);
    } catch (err) {
        if (isGoneError(err)) {
            store.closeQuestion(requestId);
            throw new QuestionGoneError(tEn('question.expired'));
        }
        throw err;
    }
    store.recordAnswer(requestId, shown);
    store.closeQuestion(requestId);
}

/**
 * What a typed reply means for a waiting question: the option it names (by
 * label, value or its 1-based number), the text itself when any text will do,
 * or null when it matches no choice of a choose-only question.
 */
export function resolveTypedAnswer(text: string, question: Pick<LiveQuestion, 'options' | 'allowFreeText'>): { value: string; shown: string } | null {
    const typed = text.trim();
    const options = question.options ?? [];
    const lower = typed.toLowerCase();
    const index = /^\d+$/.test(typed) ? Number(typed) - 1 : -1;
    const picked = options[index] ?? options.find((o) => o.label.toLowerCase() === lower || o.value.toLowerCase() === lower);
    if (picked) return { value: picked.value, shown: picked.label };
    if (question.allowFreeText === false && options.length > 0) return null;
    return { value: typed, shown: typed };
}

/** The question the chat's message box answers: the newest one waiting in this chat, unless it is sensitive. */
export function waitingQuestionId(live: Record<string, LiveQuestion>, sessionId: string): string | null {
    let found: string | null = null;
    for (const [id, q] of Object.entries(live)) {
        if (q.sessionId === sessionId && !q.sensitive) found = id;
    }
    return found;
}

/** "5 min" / "40 s" left on a question with a deadline, ticking while it shows. */
function useTimeLeft(expiresAt: number | null | undefined): number | null {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!expiresAt) return;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [expiresAt]);
    return expiresAt ? Math.max(0, expiresAt - now) : null;
}

export function NeedsInputCard({ data }: { data: unknown }) {
    const t = useT();
    const [state, setState] = useState<SubmitState>({ status: 'pending' });
    const [freeText, setFreeText] = useState('');
    const requestId = isNeedsInputPayload(data) ? data.request_id : '';
    const live = useChatStore((s) => s.liveQuestions[requestId]);
    const isAnswered = useChatStore((s) => requestId in s.questionAnswers);
    const answerShown = useChatStore((s) => s.questionAnswers[requestId] ?? null);
    const closeQuestion = useChatStore((s) => s.closeQuestion);
    const left = useTimeLeft(live?.expiresAt);

    // The backend stops waiting at its own deadline; close the card then too.
    useEffect(() => {
        if (!live || live.expiresAt === null) return;
        const remaining = live.expiresAt - Date.now();
        if (remaining <= 0) {
            closeQuestion(requestId);
            return;
        }
        const timer = setTimeout(() => closeQuestion(requestId), remaining);
        return () => clearTimeout(timer);
    }, [live, requestId, closeQuestion]);

    if (!isNeedsInputPayload(data)) {
        return <UnsupportedCard variant="invalid" render="needs_input" data={data} />;
    }

    const submitting = state.status === 'submitting';

    const submit = async (value: string, shown: string) => {
        if (!value.trim() || submitting || !live) return;
        setState({ status: 'submitting' });
        try {
            await answerQuestion(data.session_id, data.request_id, value, data.sensitive ? null : shown);
            setState({ status: 'pending' });
        } catch (err) {
            if (err instanceof QuestionGoneError) {
                setState({ status: 'pending' });
                return;
            }
            setState({ status: 'error', message: err instanceof Error ? err.message : t('question.sendFailed') });
        }
    };

    if (isAnswered) {
        return (
            <div className="render-needs-input render-needs-input--answered">
                {/* Formatting only: question text can carry tool-derived content. */}
                <QuestionText text={data.question} />
                <p className="render-needs-input__answered-note" role="status">
                    {answerShown === null || data.sensitive
                        ? t('question.youAnsweredHidden')
                        : t('question.youAnswered', { answer: answerShown })}
                </p>
            </div>
        );
    }

    if (!live && !submitting) {
        return (
            <div className="render-needs-input render-needs-input--expired" aria-disabled="true">
                <div className="render-needs-input__header">
                    <span className="render-needs-input__badge">{t('question.badge')}</span>
                </div>
                <QuestionText text={data.question} />
                <p className="render-needs-input__expired-note" role="status">{t('question.expired')}</p>
            </div>
        );
    }

    return (
        <div className="render-needs-input">
            <div className="render-needs-input__header">
                <span className="render-needs-input__badge">{t('question.badge')}</span>
                {data.sensitive && (
                    <span className="render-needs-input__sensitive-badge">{t('question.sensitive')}</span>
                )}
            </div>
            <QuestionText text={data.question} />
            {data.options.length > 0 && (
                <div className="render-needs-input__options">
                    {data.options.map((opt) => (
                        <button
                            key={opt.value}
                            type="button"
                            className="render-needs-input__option"
                            disabled={submitting}
                            title={opt.description || undefined}
                            onClick={() => submit(opt.value, opt.label)}
                        >
                            <bdi>{opt.label}</bdi>
                        </button>
                    ))}
                </div>
            )}
            {data.allow_free_text && data.sensitive && (
                <form
                    className="render-needs-input__free-text"
                    onSubmit={(e) => {
                        e.preventDefault();
                        submit(freeText, freeText);
                    }}
                >
                    <input
                        type="password"
                        className="render-needs-input__free-text-input"
                        value={freeText}
                        disabled={submitting}
                        placeholder={t('question.hiddenPlaceholder', { mask: SENSITIVE_PLACEHOLDER })}
                        onChange={(e) => setFreeText(e.target.value)}
                        autoComplete="off"
                        autoFocus
                    />
                    <button
                        type="submit"
                        className="render-needs-input__free-text-submit"
                        disabled={submitting || !freeText.trim()}
                    >
                        {t('question.send')}
                    </button>
                </form>
            )}
            {data.allow_free_text && !data.sensitive && (
                <p className="render-needs-input__hint">
                    {data.options.length > 0 ? t('question.typeHint') : t('question.typeHintNoOptions')}
                </p>
            )}
            {left !== null && left > 0 && (
                <p className="render-needs-input__hint">
                    {t('question.timeLeft', { time: spokenDuration(left >= 60_000 ? Math.ceil(left / 60_000) * 60_000 : left, t.bare) })}
                </p>
            )}
            {state.status === 'error' && (
                <p className="render-needs-input__error">{state.message}</p>
            )}
        </div>
    );
}
