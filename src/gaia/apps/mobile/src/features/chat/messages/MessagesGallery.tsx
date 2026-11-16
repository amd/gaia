// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AssistantBubble } from './AssistantBubble';
import { MessageListView } from './MessageList';
import { UserBubble } from './UserBubble';
import type { StartOpen } from './useMessageActions';
import { RecordingProgress } from '../recording/RecordingProgress';
import { GuideCard } from '../cards/GuideCard';
import { FIXTURES, GUIDE_WORKOUT, type MessagesFixture } from './__fixtures__/messages';
import { Screen } from '@/ui';

/** Developer gallery of the message list in each state; not part of the shipped screens. */
export type MessagesGalleryState = MessagesFixture | StartOpen | 'guided';

export function MessagesGallery({ state }: { state: MessagesGalleryState }) {
    if (state === 'actions' || state === 'delete' || state === 'edit') {
        const [question] = FIXTURES.short.messages;
        return (
            <Screen>
                <UserBubble message={question} context={{ laterTurns: 2 }} startOpen={state} />
            </Screen>
        );
    }
    if (state === 'guided') {
        return (
            <Screen>
                <GuideCard data={GUIDE_WORKOUT} startGuided />
            </Screen>
        );
    }
    const fixture = FIXTURES[state];
    return (
        <Screen>
            <MessageListView
                messages={fixture.messages}
                loading={state === 'loading'}
                live={fixture.live ? <AssistantBubble message={fixture.live} streaming /> : fixture.events ? <RecordingProgress events={fixture.events} /> : undefined}
            />
        </Screen>
    );
}
