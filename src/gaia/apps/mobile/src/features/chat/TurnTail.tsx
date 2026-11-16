// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo } from 'react';
import { View } from 'react-native';

import { cardsFromEvents } from '@webui/utils/renderCards.core';

import { ModelServerDownNotice } from '@/features/model/ModelServerDownNotice';
import { useT } from '@/i18n/useT';
import { useAnnounce } from '@/platform/useAnnounce';
import { Button, Notice, Row, Spinner, Stack, Text } from '@/ui';
import { RotateCcw } from '@/ui/icons';

import { ActivityTimeline } from './activity/ActivityTimeline';
import { stepsFromEvents } from './activity/stepsFromEvents';
import { AnswerCards } from './cards/RenderCard';
import { chatErrorText, isModelServerDown } from './chatErrors';
import { StoppedNote } from './EndingNotes';
import { Markdown } from './markdown/Markdown';
import { RecordingProgress } from './recording/RecordingProgress';
import type { TurnTail as Tail } from './tailState';

export interface TurnTailProps {
    tail: Tail;
    /** What the agent is doing now, or null for "Thinking...". Only used while the turn is live. */
    line: string | null;
    /** The turn is waiting on the person's answer. */
    awaitingUser?: boolean;
    onTryAgain(): void;
    onContinue(): void;
}

/** The running or just-ended turn, under the saved messages. */
export function TurnTail({ tail, line, awaitingUser = false, onTryAgain, onContinue }: TurnTailProps) {
    const t = useT();
    const steps = useMemo(() => stepsFromEvents(tail.events, t), [tail.events, t]);
    const cards = useMemo(() => cardsFromEvents(tail.events), [tail.events]);
    // A running step row already says what is happening.
    const rowRuns = steps.some((s) => s.active && s.type !== 'status');
    const showLine = tail.live && !rowRuns && (line !== null || !tail.text);
    useAnnounce(showLine ? (line ?? t('msg.thinking')) : null);
    return (
        <Stack gap="sm" paddingX="lg" paddingY="xs" testID="turn-tail">
            <ActivityTimeline steps={steps} running={tail.live} awaitingUser={awaitingUser} answer={tail.text} />
            {tail.live ? <RecordingProgress events={tail.events} /> : null}
            <AnswerCards cards={cards} />
            {tail.text ? <Markdown text={tail.text} streaming={tail.live} /> : null}
            {showLine ? (
                <View accessibilityRole="progressbar" accessibilityLiveRegion="polite">
                    <Row gap="sm">
                        <Spinner size="sm" />
                        <Text role="label" tone="secondary" numberOfLines={1} testID="live-line">
                            {line ?? t('msg.thinking')}
                        </Text>
                    </Row>
                </View>
            ) : null}
            {tail.error ? <Notice tone="danger" title={t('msg.error')} message={chatErrorText(tail.error, t)} testID="reply-error" /> : null}
            {tail.error && !tail.live && isModelServerDown(tail.error, t) ? <ModelServerDownNotice /> : null}
            {tail.stop ? <StoppedNote stop={tail.stop} /> : null}
            {tail.canContinue || tail.canTryAgain ? (
                <Row gap="sm" wrap>
                    {tail.canContinue ? <Button size="sm" label={t('msg.continue')} onPress={onContinue} testID="continue" /> : null}
                    {tail.canTryAgain ? (
                        <Button size="sm" icon={RotateCcw} label={t('msg.tryAgain')} onPress={onTryAgain} testID="try-again" />
                    ) : null}
                </Row>
            ) : null}
        </Stack>
    );
}
