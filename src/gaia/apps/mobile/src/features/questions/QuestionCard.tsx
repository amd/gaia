// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

import { useAnswerQuestion } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Badge, Button, Notice, Row, Stack, Surface, Text, TextField } from '@/ui';

import {
    isQuestionGone,
    SENSITIVE_MASK,
    timeLeftText,
    type QuestionCardData,
    type QuestionOption,
} from './questionLogic';
import { useResolvedPrompt } from '@/state/chatStreams';

import { QuestionText } from './QuestionText';

export type QuestionCardState = 'live' | 'answered' | 'expired';

export interface QuestionViewProps {
    data: QuestionCardData;
    state: QuestionCardState;
    /** What was answered, for the answered state; null or absent when it was sensitive. */
    answer?: string | null;
    /** Live only: an answer is on its way. */
    submittingValue?: string | null;
    error?: string | null;
    /** Live only: ms until GAIA stops waiting. */
    leftMs?: number | null;
    onPick: (option: QuestionOption) => void;
    onSend: (text: string) => void;
}

/** The card's looks for each state; QuestionCard feeds it. */
export function QuestionCardView({ data, state, answer, submittingValue = null, error, leftMs = null, onPick, onSend }: QuestionViewProps) {
    const t = useT();
    const [secret, setSecret] = useState('');
    const submitting = submittingValue !== null;

    if (state === 'answered') {
        return (
            <Surface tone="secondary" radius="md" padding="md" border>
                <Stack gap="sm">
                    <QuestionText text={data.question} />
                    {answer === undefined && !data.sensitive ? null : (
                        <Text role="caption" tone="secondary" accessibilityRole="text">
                            {answer == null || data.sensitive ? t('question.youAnsweredHidden') : t('question.youAnswered', { answer })}
                        </Text>
                    )}
                </Stack>
            </Surface>
        );
    }

    if (state === 'expired') {
        return (
            <Surface tone="secondary" radius="md" padding="md" border>
                <Stack gap="sm">
                    <Badge label={t('question.badge')} />
                    <QuestionText text={data.question} />
                    <Text role="caption" tone="muted">
                        {t('question.expired')}
                    </Text>
                </Stack>
            </Surface>
        );
    }

    return (
        <Surface tone="card" radius="md" padding="md" border>
            <Stack gap="md">
                <Row gap="sm" wrap>
                    <Badge label={t('question.badge')} tone="accent" />
                    {data.sensitive ? <Badge label={t('question.sensitive')} tone="warning" /> : null}
                </Row>
                <QuestionText text={data.question} />
                {data.options.length > 0 ? (
                    <Stack gap="sm">
                        {data.options.map((option) => (
                            <Stack key={option.value} gap="xs">
                                <Button
                                    label={option.label}
                                    variant="secondary"
                                    full
                                    disabled={submitting}
                                    busy={submittingValue === option.value}
                                    onPress={() => onPick(option)}
                                />
                                {option.description ? (
                                    <Text role="caption" tone="muted">
                                        {option.description}
                                    </Text>
                                ) : null}
                            </Stack>
                        ))}
                    </Stack>
                ) : null}
                {data.allowFreeText && data.sensitive ? (
                    <Stack gap="sm">
                        <TextField
                            value={secret}
                            onChangeText={setSecret}
                            secureTextEntry
                            disabled={submitting}
                            placeholder={t.bare('question.hiddenPlaceholder', { mask: SENSITIVE_MASK })}
                            accessibilityLabel={t('question.hiddenPlaceholder', { mask: SENSITIVE_MASK })}
                            returnKeyType="send"
                            onSubmitEditing={() => secret.trim() && onSend(secret)}
                        />
                        <Button
                            label={t('question.send')}
                            variant="primary"
                            disabled={submitting || !secret.trim()}
                            busy={submittingValue !== null && data.options.every((o) => o.value !== submittingValue)}
                            onPress={() => onSend(secret)}
                        />
                    </Stack>
                ) : null}
                {data.allowFreeText && !data.sensitive ? (
                    <Text role="caption" tone="secondary">
                        {data.options.length > 0 ? t('question.typeHint') : t('question.typeHintNoOptions')}
                    </Text>
                ) : null}
                {leftMs !== null && leftMs > 0 ? (
                    <Text role="caption" tone="secondary">
                        {timeLeftText(leftMs, t)}
                    </Text>
                ) : null}
                {error ? <Notice tone="danger" title={t('question.sendFailed')} message={error} /> : null}
            </Stack>
        </Surface>
    );
}

export interface QuestionCardProps {
    data: QuestionCardData;
}

function useNow(active: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!active) return undefined;
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [active]);
    return now;
}

/** GAIA's question inside the chat. Option taps and the sensitive field answer here; the message box answers through the chat view. */
export function QuestionCard({ data }: QuestionCardProps) {
    const t = useT();
    const mutation = useAnswerQuestion();
    const resolved = useResolvedPrompt(data.sessionId, data.requestId);
    const [gone, setGone] = useState(false);
    const [submittingValue, setSubmittingValue] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const ticking = !resolved && !gone && data.expiresAt !== null;
    const now = useNow(ticking);
    // The countdown shows expiry before the turn ends and the store says so.
    const pastDeadline = ticking && data.expiresAt !== null && now >= data.expiresAt;

    const submit = (value: string, shown: string) => {
        if (!value.trim() || submittingValue !== null) return;
        setSubmittingValue(value);
        setError(null);
        mutation
            .mutateAsync({ session_id: data.sessionId, request_id: data.requestId, value, display: shown })
            .catch((err: unknown) => {
                if (isQuestionGone(err)) setGone(true);
                else setError(describeError(err, t));
            })
            .finally(() => setSubmittingValue(null));
    };

    let state: QuestionCardState = 'live';
    if (resolved?.state === 'answered') state = 'answered';
    else if (resolved?.state === 'expired' || gone || pastDeadline) state = 'expired';

    return (
        <QuestionCardView
            data={data}
            state={state}
            answer={resolved?.answer}
            submittingValue={submittingValue}
            error={error}
            leftMs={data.expiresAt === null ? null : Math.max(0, data.expiresAt - now)}
            onPick={(option) => submit(option.value, option.label)}
            onSend={(text) => submit(text, text)}
        />
    );
}
