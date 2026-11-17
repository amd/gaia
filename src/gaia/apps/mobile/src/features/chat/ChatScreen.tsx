// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef, useState } from 'react';

import { nextToSend } from '@webui/utils/messageQueue';
import { FLAGSHIP_AGENT_ID } from '@webui/utils/newTask';
import { MAX_STREAM_RECONNECTS } from '@webui/utils/streamErrors.core';

import { useAnswerQuestion, useChatStream, useMessages, useSession } from '@/data';
import { Composer, useComposerQueue } from '@/features/composer';
import { QueuedMessages } from '@/features/composer/QueuedMessages';
import { describeError, describeErrorWith, useT } from '@/i18n/useT';
import { isQuestionGone, questionCardData, resolveTypedAnswer } from '@/features/questions/questionLogic';
import { ErrorState, Notice, Screen, Stack } from '@/ui';

import { ChatPrompts } from './ChatPrompts';
import { HistoryNotice, historyNoticeText } from './HistoryNotice';
import { ChatHeader } from './header';
import { PermissionPrompt } from './permission/PermissionPrompt';
import { usePermissionPrompt } from './permission/usePermissionPrompt';
import { liveStatusLine } from './liveStatus';
import { MessageList } from './messages/MessageList';
import { TailIssues } from './ReplyErrors';
import { registerOpenChat } from './sendToOpenChat';
import { buildTail, serverDownReplyId } from './tailState';
import { TurnTail } from './TurnTail';
import { useTurnAnnouncer } from './useTurnAnnouncer';

const NO_MESSAGES: never[] = [];

export function ChatScreen({ sessionId }: { sessionId: string }) {
    const t = useT();
    const session = useSession(sessionId);
    const messages = useMessages(sessionId);
    const stream = useChatStream(sessionId);
    const queue = useComposerQueue(sessionId);
    const permission = usePermissionPrompt(sessionId);
    const answer = useAnswerQuestion();
    const stopped = useRef(false);
    const [actionError, setActionError] = useState<unknown>(null);
    const busy = stream.status !== 'idle';
    const historyNotice = historyNoticeText(stream.events);
    const saved = messages.data?.messages ?? NO_MESSAGES;
    useTurnAnnouncer(stream, saved);

    // A chat made by a retired agent can be read but not continued.
    const retiredAgent = session.data?.agent_type && session.data.agent_type !== FLAGSHIP_AGENT_ID ? session.data.agent_type : null;

    const builtTail = buildTail(saved, stream);
    const tail = builtTail && retiredAgent ? { ...builtTail, canContinue: false } : builtTail;
    const line = stream.reconnecting
        ? t('live.reconnectingStream', { attempt: stream.reconnecting, max: MAX_STREAM_RECONNECTS })
        : liveStatusLine(
              stream.events,
              { waitingOnPerson: !!stream.pendingPermission || !!stream.pendingQuestion, answering: stream.liveText !== '' },
              t,
          );

    const question = stream.pendingQuestion && !stream.pendingQuestion.sensitive ? stream.pendingQuestion : null;

    // Messages typed while a reply runs go out one by one once it ends; after Stop they return to the box.
    const sendable = nextToSend(queue.held) !== undefined;
    const { flush, giveBack } = queue;
    useEffect(() => {
        if (busy) return;
        if (stopped.current) {
            stopped.current = false;
            giveBack();
            return;
        }
        if (sendable) void flush(sessionId);
    }, [busy, sendable, flush, giveBack, sessionId]);

    const stop = () => {
        stopped.current = true;
        stream.stop();
    };

    const answerQuestion = (message: string, asked: NonNullable<typeof question>): boolean => {
        const data = questionCardData(sessionId, asked);
        const resolved = data && resolveTypedAnswer(message, data);
        if (!resolved) {
            setActionError(t('composer.pickAnOption'));
            return false;
        }
        answer
            .mutateAsync({ session_id: sessionId, request_id: asked.request_id, value: resolved.value, display: message })
            .catch((err: unknown) => {
                // Back in the box, so nothing typed is lost; an expired question says so in its card.
                queue.setDraft((prev) => (prev.trim() ? `${message}\n\n${prev}` : message));
                if (!isQuestionGone(err)) setActionError(t.compose((say) => say('composer.answerFailed', { error: describeErrorWith(err, say) })));
            });
        return true;
    };

    const send = (message: string): boolean | void => {
        setActionError(null);
        if (retiredAgent) {
            setActionError(t('chat.retiredSend', { agent: retiredAgent }));
            return false;
        }
        if (question) return answerQuestion(message, question);
        if (busy) queue.hold(message);
        else stream.send(message);
    };
    const sendRef = useRef(send);
    useEffect(() => {
        sendRef.current = send;
    });
    useEffect(() => registerOpenChat((text) => void sendRef.current(text)), []);

    const continueReply = () => {
        setActionError(null);
        stream.send(t('msg.continuePrompt'));
    };

    const placeholder = question
        ? t('composer.answerQuestion')
        : busy
          ? t('composer.queueNext')
          : saved.length > 0 || tail
            ? t('composer.reply')
            : undefined;
    const disabledReason = retiredAgent ? t('chat.retiredReadOnly', { agent: retiredAgent }) : undefined;

    return (
        <Screen
            keyboard="avoid"
            footerBleed
            header={<ChatHeader sessionId={sessionId} />}
            footer={
                <>
                    {actionError ? (
                        <Stack paddingX="lg">
                            <Notice
                                tone="danger"
                                message={describeError(actionError, t)}
                                action={{ label: t('chat.dismissError'), onPress: () => setActionError(null) }}
                                testID="action-error"
                            />
                        </Stack>
                    ) : null}
                    {permission ? (
                        <Stack paddingX="gutter">
                            <PermissionPrompt key={permission.prompt.id} {...permission} />
                        </Stack>
                    ) : null}
                    <QueuedMessages items={queue.held} replying={busy} onRemove={queue.remove} onRetry={queue.retry} />
                    <Composer
                        value={queue.draft}
                        onChange={queue.setDraft}
                        onSend={send}
                        onStop={stop}
                        running={busy}
                        answering={!!question}
                        placeholder={placeholder}
                        disabledReason={disabledReason}
                    />
                </>
            }
        >
            {session.isError ? (
                <ErrorState title={describeError(session.error, t)} onRetry={() => void session.refetch()} />
            ) : (
                <MessageList
                    sessionId={sessionId}
                    fixShownBelowId={serverDownReplyId(tail)}
                    live={
                        <>
                            {historyNotice ? <HistoryNotice text={historyNotice} /> : null}
                            {tail ? (
                                <TurnTail
                                    tail={tail}
                                    line={line}
                                    awaitingUser={!!stream.pendingPermission || !!stream.pendingQuestion}
                                    onContinue={continueReply}
                                />
                            ) : null}
                            {tail ? <TailIssues tail={tail} local={session.data?.inference_remote === false} /> : null}
                            <ChatPrompts sessionId={sessionId} question={stream.pendingQuestion} confirmation={stream.pendingConfirmation} />
                        </>
                    }
                />
            )}
        </Screen>
    );
}
