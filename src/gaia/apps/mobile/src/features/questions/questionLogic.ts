// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { spokenDuration } from '@webui/utils/spokenDuration.core';

import type { Translate } from '@/i18n/useT';

export interface QuestionOption {
    value: string;
    label: string;
    description?: string;
}

/** What a needs_input event asks, plus when GAIA stops waiting. */
export interface QuestionCardData {
    sessionId: string;
    requestId: string;
    question: string;
    options: readonly QuestionOption[];
    allowFreeText: boolean;
    sensitive: boolean;
    /** Epoch ms when GAIA stops waiting, or null for no deadline. */
    expiresAt: number | null;
}

export const SENSITIVE_MASK = '••••••';

/** The card's data from the stream's needs_input event; null when it carries no id to answer by. */
export function questionCardData(sessionId: string, event: StreamEvent, receivedAt: number = Date.now()): QuestionCardData | null {
    if (event.type !== 'needs_input' || !event.request_id) return null;
    const deadline = event.expires_at != null ? event.expires_at * 1000 : null;
    const timeout = typeof event.timeout_seconds === 'number' && event.timeout_seconds > 0 ? receivedAt + event.timeout_seconds * 1000 : null;
    return {
        sessionId,
        requestId: event.request_id,
        question: typeof event.question === 'string' ? event.question : '',
        options: Array.isArray(event.options) ? event.options : [],
        allowFreeText: event.allow_free_text !== false,
        sensitive: Boolean(event.sensitive),
        expiresAt: deadline ?? timeout,
    };
}

/**
 * What a typed reply means for a waiting question: the option it names (by label, value or
 * 1-based number), the text itself when any text will do, or null when it matches no choice
 * of a choose-only question.
 */
export function resolveTypedAnswer(
    text: string,
    question: Pick<QuestionCardData, 'options' | 'allowFreeText'>,
): { value: string; shown: string } | null {
    const typed = text.trim();
    const lower = typed.toLowerCase();
    const index = /^\d+$/.test(typed) ? Number(typed) - 1 : -1;
    const picked = question.options[index] ?? question.options.find((o) => o.label.toLowerCase() === lower || o.value.toLowerCase() === lower);
    if (picked) return { value: picked.value, shown: picked.label };
    if (!question.allowFreeText && question.options.length > 0) return null;
    return { value: typed, shown: typed };
}

/** The answer endpoint's 404: GAIA no longer waits on this question. A 409 may still succeed. */
export function isQuestionGone(err: unknown): boolean {
    return err instanceof GaiaApiError && err.status === 404;
}

/** "GAIA waits 5 min more for an answer." Minutes round up, so the line doesn't claim less time than is left. */
export function timeLeftText(leftMs: number, t: Translate): string {
    const rounded = leftMs >= 60_000 ? Math.ceil(leftMs / 60_000) * 60_000 : leftMs;
    return t('question.timeLeft', { time: spokenDuration(rounded, t.bare) });
}
