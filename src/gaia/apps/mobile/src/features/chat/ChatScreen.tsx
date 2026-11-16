// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { connectCardProviders, hasConnectCard } from '@webui/utils/connectAccountCard.core';
import { isAuthRequiredMessage } from '@webui/utils/connectorRetry.core';
import { useEffect, useRef, useState } from 'react';

import { needsOutcome, nextToSend } from '@webui/utils/messageQueue';
import { FLAGSHIP_AGENT_ID } from '@webui/utils/newTask';
import { MAX_STREAM_RECONNECTS } from '@webui/utils/streamErrors.core';

import { useChatReadAloud } from '@/features/audio/useChatReadAloud';
import { useAnswerQuestion, useChatStream, useContextUsage, usePermissionMode, useMessages, useRefreshTurnChanges, useSession } from '@/data';
import { FlagshipAgentBanner } from '@/features/banners/FlagshipAgentBanner';
import { Composer, ContextMeter, ContextWarning, ModelChip, PermissionChip, SpendMeter, useAttachments, useComposerQueue, useFollowUpEvents } from '@/features/composer';
import { QueuedMessages } from '@/features/composer/QueuedMessages';
import { FileScope } from '@/features/files';
import { describeError, describeErrorWith, useT } from '@/i18n/useT';
import { isQuestionGone, questionCardData, resolveTypedAnswer } from '@/features/questions/questionLogic';
import { ErrorState, Notice, Screen, Stack } from '@/ui';

import { useAnswerComparison } from './compare/useAnswerComparison';
import { ChatPrompts } from './ChatPrompts';
import { ConnectorRetry } from './ConnectorRetry';
import { HistoryNotice, historyNoticeText } from './HistoryNotice';
import { ChatHeader } from './header';
import { PermissionPrompt } from './permission/PermissionPrompt';
import { usePermissionPrompt } from './permission/usePermissionPrompt';
import { liveStatusLine } from './liveStatus';
import { MessageList } from './messages/MessageList';
import { TailIssues } from './ReplyErrors';
import { registerOpenChat } from './sendToOpenChat';
import { OfflineNotice, SessionPanel } from './session-panel';
import { buildTail, serverDownReplyId } from './tailState';
import { TurnTail } from './TurnTail';
import { useTurnAnnouncer, useTurnEnd } from './useTurnAnnouncer';

const NO_MESSAGES: never[] = [];

export function ChatScreen({ sessionId, onManageDocuments }: { sessionId: string; onManageDocuments?: () => void }) {
    const t = useT();
    const session = useSession(sessionId);
    const messages = useMessages(sessionId);
    const stream = useChatStream(sessionId);
    const queue = useComposerQueue(sessionId);
    const attachments = useAttachments();
    const permissionMode = usePermissionMode(sessionId);
    const permission = usePermissionPrompt(sessionId);
    const answer = useAnswerQuestion();
    const followUps = useFollowUpEvents(sessionId, stream.events);
    const stopped = useRef(false);
    const [actionError, setActionError] = useState<unknown>(null);
    const [panelOpen, setPanelOpen] = useState(false);
    const busy = stream.status !== 'idle';
    // A comparison waiting on its model holds new messages like a running reply does.
    const { comparing } = useAnswerComparison(sessionId);
    const hold = busy || comparing;
    const context = useContextUsage(sessionId, stream.events, busy);
    const historyNotice = historyNoticeText(stream.events);
    const saved = messages.data?.messages ?? NO_MESSAGES;
    useChatReadAloud(sessionId, stream, saved);
    useTurnAnnouncer(stream, saved);
    const refreshChanges = useRefreshTurnChanges(sessionId);
    useTurnEnd(busy, () => void refreshChanges());

    // A chat made by a retired agent can be read but not continued.
    const retiredAgent = session.data?.agent_type && session.data.agent_type !== FLAGSHIP_AGENT_ID ? session.data.agent_type : null;

    const builtTail = buildTail(saved, stream);
    const tail = builtTail && retiredAgent ? { ...builtTail, canTryAgain: false, canContinue: false } : builtTail;
    const lastReply = saved.filter((m) => m.role !== 'system').at(-1);
    const connectProviders = connectCardProviders(lastReply);
    // A Connect card names its connectors outright; a reply with only its wording is read for them.
    const connectorError =
        !busy &&
        !retiredAgent &&
        lastReply?.role === 'assistant' &&
        (hasConnectCard(lastReply) ? connectProviders.length > 0 : isAuthRequiredMessage(lastReply.content))
            ? lastReply
            : null;
    const line = stream.reconnecting
        ? t('live.reconnectingStream', { attempt: stream.reconnecting, max: MAX_STREAM_RECONNECTS })
        : liveStatusLine(
              stream.events,
              { waitingOnPerson: !!stream.pendingPermission || !!stream.pendingQuestion, answering: stream.liveText !== '' },
              t,
          );

    const question = stream.pendingQuestion && !stream.pendingQuestion.sensitive ? stream.pendingQuestion : null;
    const steerable = stream.status === 'streaming' && !followUps.unavailable;

    // Messages typed while a reply runs go out one by one once it ends; after Stop they return to the box.
    const sendable = nextToSend(queue.held) !== undefined || needsOutcome(queue.held);
    const { flush, giveBack } = queue;
    useEffect(() => {
        if (hold) return;
        if (stopped.current) {
            stopped.current = false;
            giveBack();
            return;
        }
        if (sendable) void flush(sessionId);
    }, [hold, sendable, flush, giveBack, sessionId]);

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
        if (hold) queue.hold(message);
        else stream.send(message);
    };
    const sendRef = useRef(send);
    useEffect(() => {
        sendRef.current = send;
    });
    useEffect(() => registerOpenChat((text) => void sendRef.current(text)), []);

    const tryAgain = (replyId: number | null) => {
        setActionError(null);
        stream.retry(replyId).catch((err: unknown) => setActionError(err));
    };
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
        <FileScope sessionId={sessionId}>
        <Screen
            keyboard="avoid"
            footerBleed
            header={<ChatHeader sessionId={sessionId} onOpenSessionPanel={() => setPanelOpen(true)} onManageDocuments={onManageDocuments} />}
            footer={
                <>
                    <FlagshipAgentBanner />
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
                    {connectorError ? <ConnectorRetry content={connectorError.content} providers={connectProviders} onRetry={() => tryAgain(connectorError.id)} /> : null}
                    {permission ? <PermissionPrompt key={permission.prompt.id} {...permission} /> : null}
                    <QueuedMessages
                        items={queue.held}
                        replying={busy}
                        onRemove={queue.remove}
                        onRetry={queue.retry}
                        onSteer={steerable ? queue.steer : undefined}
                    />
                    <OfflineNotice sessionId={sessionId} live={busy} />
                    <ContextWarning usage={context.usage} />
                    <Composer
                        value={queue.draft}
                        onChange={queue.setDraft}
                        onSend={send}
                        onStop={stop}
                        running={busy}
                        attachments={attachments}
                        leftControls={
                            <PermissionChip mode={permissionMode.mode ?? 'ask'} onChange={(mode) => permissionMode.set.mutate(mode)} />
                        }
                        answering={!!question}
                        placeholder={placeholder}
                        disabledReason={disabledReason}
                        rightControls={
                            <>
                                <SpendMeter sessionId={sessionId} busy={busy} />
                                <ContextMeter usage={context.usage} unavailable={context.unavailable} onDetails={() => setPanelOpen(true)} />
                                <ModelChip turnRunning={busy} />
                            </>
                        }
                    />
                </>
            }
        >
            {session.isError ? (
                <ErrorState title={describeError(session.error, t)} onRetry={() => void session.refetch()} />
            ) : (
                <MessageList
                    sessionId={sessionId}
                    readOnly={!!retiredAgent}
                    fixShownBelowId={serverDownReplyId(tail)}
                    live={
                        <>
                            {historyNotice ? <HistoryNotice text={historyNotice} /> : null}
                            {tail ? (
                                <TurnTail
                                    tail={tail}
                                    line={line}
                                    awaitingUser={!!stream.pendingPermission || !!stream.pendingQuestion}
                                    onTryAgain={() => tryAgain(tail.replyId)}
                                    onContinue={continueReply}
                                />
                            ) : null}
                            {tail ? <TailIssues tail={tail} local={session.data?.inference_remote === false} /> : null}
                            <ChatPrompts sessionId={sessionId} question={stream.pendingQuestion} confirmation={stream.pendingConfirmation} />
                        </>
                    }
                />
            )}
            <SessionPanel sessionId={sessionId} open={panelOpen} onClose={() => setPanelOpen(false)} />
        </Screen>
        </FileScope>
    );
}
