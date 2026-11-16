// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import * as Clipboard from 'expo-clipboard';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import type { Message } from '@/api/client.types';
import { GaiaConfigError } from '@/api/errors';
import { isErrorContent } from '@webui/utils/answerErrors.core';

import { queryKeys, useGaiaClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ActionSheet, Button, Notice, Sheet, Stack, Text, type SheetAction } from '@/ui';
import { Copy, Pencil, RefreshCw, Shuffle, Trash2 } from '@/ui/icons';

import { RetrySheet } from '../compare/RetrySheet';
import { useAnswerComparison } from '../compare/useAnswerComparison';
import { useMessageActionsContext } from './MessageActionsContext';
import { MessageEditor } from './MessageEditor';
import { formatFullTimestamp } from './time';

const COPIED_MS = 2000;

/** Where a message sits in its chat, so Resend and Edit can say what else goes. */
export interface MessageContext {
    /** Messages from the next question on; Resend and Edit remove them. */
    laterTurns: number;
    /** The newest message in the chat. */
    latest?: boolean;
}

/** What a bubble starts with open; for the dev gallery. */
export type StartOpen = 'actions' | 'delete' | 'edit';

const ALONE: MessageContext = { laterTurns: 0 };
const NO_ACTIONS: SheetAction[] = [];

export interface MessageActions {
    copy(): void;
    copied: boolean;
    /** The sheets, closed until opened; render once beside the bubble. */
    sheet: ReactNode;
    openSheet(): void;
    /** Spread onto the Pressable that long-press opens the sheet from. */
    a11y: {
        accessibilityActions: readonly { name: string; label: string }[];
        onAccessibilityAction: (event: { nativeEvent: { actionName: string } }) => void;
    };
    /** The edit box, shown in place of the text while the message is being edited. */
    editor: ReactNode;
    /** Shown when an action failed. */
    notice: ReactNode;
}

// Try again is the turn tail's button. `extra` goes before Copy (a reply's Read aloud).
export function useMessageActions(
    message: Message,
    copyText: string,
    context: MessageContext = ALONE,
    startOpen?: StartOpen,
    extra: SheetAction[] = NO_ACTIONS,
): MessageActions {
    const t = useT();
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const { send, busy, readOnly } = useMessageActionsContext();
    const { comparison, retryWith } = useAnswerComparison(message.session_id);
    const [retryOpen, setRetryOpen] = useState(false);
    const [open, setOpen] = useState(startOpen === 'actions');
    const [copied, setCopied] = useState(false);
    const [failure, setFailure] = useState<string | null>(null);
    const [editing, setEditing] = useState(startOpen === 'edit');
    const [sending, setSending] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(startOpen === 'delete');
    const [drop, setDrop] = useState<string | null>(null);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(
        () => () => {
            if (timer.current) clearTimeout(timer.current);
        },
        [],
    );

    const copy = useCallback(() => {
        Clipboard.setStringAsync(copyText).then(
            () => {
                setFailure(null);
                setCopied(true);
                if (timer.current) clearTimeout(timer.current);
                timer.current = setTimeout(() => setCopied(false), COPIED_MS);
            },
            (error: unknown) => setFailure(describeError(error, t)),
        );
    }, [copyText, t]);

    // A message still showing under a temporary id isn't on the PC yet.
    const saved = message.id > 0;
    const sessionId = message.session_id;

    // Removes this message (and, to send it again, everything after it), then sends `text` when given.
    const removeFrom = async (text?: string): Promise<boolean> => {
        setFailure(null);
        try {
            if (!client) throw new GaiaConfigError('mobileApp.error.noConnection');
            if (text === undefined) await client.deleteMessage(sessionId, message.id);
            else await client.deleteMessagesFrom(sessionId, message.id);
            void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
            await queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
            if (text !== undefined) send(text);
            return true;
        } catch (error) {
            setFailure(describeError(error, t));
            void queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
            return false;
        }
    };

    // Sending an earlier question again drops what came after it, so ask first.
    const sendAgain = async (text: string) => {
        setSending(true);
        const sent = await removeFrom(text);
        setSending(false);
        if (sent) setEditing(false);
    };
    const askThenSend = (text: string) => {
        if (context.laterTurns > 0) setDrop(text);
        else void sendAgain(text);
    };

    const openSheet = () => setOpen(true);
    const canChange = saved && !busy;
    const isUser = message.role === 'user';
    const canRetry = !isUser && canChange && !readOnly && !!context.latest && !comparison && message.failed !== true && !isErrorContent(message.content);
    const actions: SheetAction[] = [
        ...(isUser && canChange && !readOnly
            ? [
                  { label: t('msg.edit'), icon: Pencil, onPress: () => setEditing(true) },
                  { label: t('msg.resend'), icon: RefreshCw, onPress: () => askThenSend(message.content) },
              ]
            : []),
        ...extra,
        ...(canRetry ? [{ label: t('retry.button'), icon: Shuffle, onPress: () => setRetryOpen(true) }] : []),
        ...(copyText ? [{ label: t('msg.copy'), icon: Copy, onPress: copy }] : []),
        ...(canChange ? [{ label: t('msg.delete'), icon: Trash2, destructive: true, onPress: () => setConfirmDelete(true) }] : []),
    ];

    // Each language half formats its own date, so the bracketed English isn't an Arabic date.
    const title = Number.isNaN(new Date(message.created_at).getTime())
        ? undefined
        : t.compose((say, code) =>
              say(message.role === 'user' ? 'msg.sent' : 'msg.replied', { time: formatFullTimestamp(message.created_at, t, code) }),
          );

    const confirm = (heading: string, detail: string | null, go: string, onGo: () => void, onClose: () => void, destructive = false) => (
        <Sheet open onClose={onClose} title={heading}>
            <Stack gap="md">
                {detail ? <Text role="body">{detail}</Text> : null}
                <Button label={go} variant={destructive ? 'danger' : 'primary'} full onPress={() => { onClose(); onGo(); }} />
                <Button label={t('msg.editCancel')} variant="secondary" full onPress={onClose} />
            </Stack>
        </Sheet>
    );

    return {
        copy,
        copied,
        openSheet,
        a11y: {
            accessibilityActions: [{ name: 'activate', label: t('msg.actions') }],
            onAccessibilityAction: (event) => {
                if (event.nativeEvent.actionName === 'activate') openSheet();
            },
        },
        sheet: (
            <>
                <ActionSheet open={open} onClose={() => setOpen(false)} actions={actions} title={title} />
                {canRetry ? (
                    <RetrySheet
                        open={retryOpen}
                        onClose={() => setRetryOpen(false)}
                        answeredBy={message.stats?.model}
                        onPick={(model) => retryWith(message, model)}
                    />
                ) : null}
                {confirmDelete
                    ? confirm(
                          t('msg.confirmDeleteAria'),
                          null,
                          t('msg.delete'),
                          () => void removeFrom(),
                          () => setConfirmDelete(false),
                          true,
                      )
                    : null}
                {drop !== null
                    ? confirm(
                          t.plural('msg.dropLater', context.laterTurns),
                          null,
                          t('msg.dropLaterGo'),
                          () => void sendAgain(drop),
                          () => setDrop(null),
                      )
                    : null}
            </>
        ),
        editor: editing ? (
            <MessageEditor
                original={message.content}
                onSend={askThenSend}
                onCancel={() => setEditing(false)}
                disabled={busy || sending}
            />
        ) : null,
        notice: failure ? <Notice tone="danger" message={failure} /> : null,
    };
}
