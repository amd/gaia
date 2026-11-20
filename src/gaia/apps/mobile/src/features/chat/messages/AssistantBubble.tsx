// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { cardsFromSteps } from '@webui/utils/renderCards.core';
import { SERVER_CANCELLED_TEXT, splitCutOff } from '@webui/utils/turnEnding.core';

import type { Message } from '@/api/client.types';
import { useMemo } from 'react';

import { useT } from '@/i18n/useT';
import { IconButton, Pressable, Row, Stack } from '@/ui';
import { Check, Copy, Ellipsis } from '@/ui/icons';

import { ActivityTimeline } from '../activity/ActivityTimeline';
import { AnswerFooter } from '../activity/AnswerFooter';
import { savedActivity } from '../activity/savedActivity';
import { AnswerCards } from '../cards/RenderCard';
import { CutOffNote, StoppedNote } from '../EndingNotes';
import { Markdown } from '../markdown/Markdown';
import { SavedReplyErrors } from '../ReplyErrors';
import { useMessageActions } from './useMessageActions';

export interface AssistantBubbleProps {
    message: Message;
    /** The answer is still being written: the row stays hidden. */
    streaming?: boolean;
    /** How long the reply took, from the question before it. */
    latencyMs?: number;
    /** The model-server fix under the chat explains this reply's failure. */
    fixShownBelow?: boolean;
}

// A reply with links stays walkable by element; plain prose reads as one block with its actions.
const hasLinks = (text: string) => /\]\(|https?:|`|\[/.test(text);

export function AssistantBubble({ message, streaming = false, latencyMs, fixShownBelow = false }: AssistantBubbleProps) {
    const t = useT();
    const cutOff = streaming ? null : splitCutOff(message.content);
    const cancelled = message.content.trim() === SERVER_CANCELLED_TEXT;
    const text = cancelled ? '' : cutOff ? cutOff.body : message.content;
    const activity = useMemo(() => savedActivity(message), [message]);
    const cards = message.cards ?? cardsFromSteps(message.agentSteps);
    const actions = useMessageActions(message, text);
    return (
        <Stack paddingX="lg" paddingY="xs">
            {activity.steps.length > 0 ? <ActivityTimeline steps={activity.steps} answer={text} /> : null}
            <AnswerCards cards={cards} />
            <Pressable
                onLongPress={cancelled ? undefined : actions.openSheet}
                accessibilityRole="text"
                accessible={!hasLinks(text)}
                minSize={0}
                {...(cancelled ? {} : actions.a11y)}
            >
                {text ? <Markdown text={text} streaming={streaming} /> : null}
            </Pressable>
            {!streaming && !cancelled ? <AnswerFooter latencyMs={latencyMs} stats={message.stats} ended={activity.ended} /> : null}
            {cutOff ? <CutOffNote cutOff={cutOff} /> : null}
            {cancelled ? <StoppedNote stop={{ state: 'stopped' }} /> : null}
            {!streaming && !cancelled ? <SavedReplyErrors message={message} fixShownBelow={fixShownBelow} /> : null}
            {!streaming && !cancelled ? (
                <Row>
                    <IconButton
                        icon={actions.copied ? Check : Copy}
                        label={actions.copied ? t('msg.copiedAria') : t('msg.copy')}
                        size="sm"
                        onPress={actions.copy}
                    />
                    <IconButton icon={Ellipsis} label={t('msg.actions')} size="sm" onPress={actions.openSheet} />
                </Row>
            ) : null}
            {actions.notice}
            {actions.sheet}
        </Stack>
    );
}
