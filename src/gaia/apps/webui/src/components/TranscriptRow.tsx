// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { memo } from 'react';
import { MessageBubble, isErrorContent, turnEnding } from './MessageBubble';
import { HistoryNotice, LibraryNotices } from './ChatNotices';
import { RetryWithMenu } from './RetryWithMenu';
import { AnswerComparison } from './AnswerComparison';
import { ModelServerDownNotice } from './ModelServerDownNotice';
import { ChatAgentRecovery } from './ChatAgentRecovery';
import type { Comparison } from '../hooks/useAnswerComparison';
import type { Message } from '../types';

export interface TranscriptRowProps {
    message: Message;
    className?: string;
    notice?: string;
    /** A reply is running or about to; message actions wait for it. */
    busy: boolean;
    /** The chat's agent is retired, so it can be read but not continued. */
    readOnly: boolean;
    /** The opening message, drawn before it is sent. */
    unsent: boolean;
    isLast: boolean;
    /** Any answer comparison is open in this chat. */
    comparing: boolean;
    /** The comparison under this message, if it has one. */
    comparison: Comparison | null;
    laterCount: number;
    latencyMs?: number;
    answeredBy?: string | null;
    editing: boolean;
    editDraft: string | null;
    onDelete: (messageId: number) => void;
    onResend: (message: Message) => void;
    onEditStart: (messageId: number) => void;
    onEditDraft: (text: string) => void;
    onEdit: (message: Message, text: string) => void;
    onEditCancel: () => void;
    onRegenerate: (message: Message) => void;
    onRetryWith: (message: Message, model: string) => void;
    onTryAgain: (message: Message) => void;
    onContinue: () => void;
    onKeepAnswer: (which: 'original' | 'retry') => void;
    onCloseComparison: () => void;
}

/** One message in the transcript; memoised so a streaming reply redraws only itself. */
export const TranscriptRow = memo(function TranscriptRow({
    message: msg, className, notice, busy, readOnly, unsent, isLast, comparing, comparison, laterCount, latencyMs, answeredBy,
    editing, editDraft, onDelete, onResend, onEditStart, onEditDraft, onEdit, onEditCancel, onRegenerate, onRetryWith,
    onTryAgain, onContinue, onKeepAnswer, onCloseComparison,
}: TranscriptRowProps) {
    const cutOff = msg.role === 'assistant' && turnEnding(msg.content, msg.stopState) === 'interrupted';
    const stopped = msg.role === 'assistant' && msg.stopState === 'stopped' && !msg.stopError;
    const canTryAgain = (msg.failed === true || cutOff || stopped) && !busy && !readOnly && isLast;
    const canRetry = !canTryAgain && !busy && !comparing && !readOnly
        && msg.role === 'assistant' && isLast
        && !msg.stopState && !msg.failed && !isErrorContent(msg.content);
    const canChange = !busy && !unsent && msg.role === 'user' && !readOnly;
    const model = msg.stats?.model ?? answeredBy;
    return (
        <div className={className} data-message-id={msg.id} data-role={msg.role}>
            {notice && <HistoryNotice text={notice} />}
            {msg.libraryNotices && msg.libraryNotices.length > 0 && (
                <LibraryNotices notices={msg.libraryNotices} />
            )}
            <MessageBubble
                message={msg}
                agentSteps={msg.role === 'assistant' ? msg.agentSteps : undefined}
                onDelete={!busy && !unsent ? onDelete : undefined}
                onResend={canChange ? onResend : undefined}
                onEditStart={canChange ? () => onEditStart(msg.id) : undefined}
                editing={editing}
                editDraft={editDraft}
                onEditDraft={onEditDraft}
                onEdit={onEdit}
                onEditCancel={onEditCancel}
                laterCount={laterCount}
                onRegenerate={canRetry && !unsent ? () => onRegenerate(msg) : undefined}
                extraActions={canRetry ? (
                    <RetryWithMenu answeredBy={model} onPick={(picked) => onRetryWith(msg, picked)} />
                ) : undefined}
                onTryAgain={canTryAgain ? () => onTryAgain(msg) : undefined}
                onContinue={canTryAgain && stopped ? onContinue : undefined}
                latest={msg.role === 'assistant' && isLast}
                latencyMs={latencyMs}
            />
            {msg.failure === 'model_server_down' && isLast && !busy && <ModelServerDownNotice />}
            {msg.failed === true && msg.failure !== 'model_server_down' && canTryAgain && (
                <ChatAgentRecovery key={msg.id} onTryAgain={() => onTryAgain(msg)} />
            )}
            {comparison && (
                <AnswerComparison
                    original={msg}
                    originalModel={model}
                    originalLatencyMs={latencyMs}
                    state={comparison.state}
                    keeping={comparison.keeping}
                    onKeep={onKeepAnswer}
                    onClose={onCloseComparison}
                />
            )}
        </div>
    );
});
