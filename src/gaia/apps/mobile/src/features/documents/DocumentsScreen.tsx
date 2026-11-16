// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';

import { useAttachDocument, useCancelIndexing, useDocumentRemoval, useDocuments, useIndexingView, useReindexDocument, useSession } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeErrorWith, useT } from '@/i18n/useT';
import { EmptyState, ErrorState, IconButton, List, ListSkeleton, Notice, Row, Screen, Stack, Text, useTheme } from '@/ui';
import { X } from '@/ui/icons';

import { AddDocuments } from './AddDocuments';
import { DocumentRow } from './DocumentRow';
import { libraryStats } from './libraryStats';
import { useNextChatDocs } from './nextChatDocs';
import { TargetChooser, type LibraryTarget } from './TargetChooser';

export interface DocumentsScreenProps {
    // The chat the library was opened from, if any.
    chatId?: string;
    // Opens the PC's file browser for a chat.
    onBrowseFiles?: (chatId: string) => void;
    // Opens a library document's preview and share screen, from the chat the library was opened from.
    onPreview?: (chatId: string, docId: string, name: string) => void;
}

// A failed library action (remove, cancel) shows here until dismissed; only one shows at a time.
export function useActionError() {
    const [error, setError] = useState<string | null>(null);
    return { error, report: setError, dismiss: () => setError(null) };
}

interface OpenChat {
    id: string;
    /** Quoted, for messages. */
    name: string;
    title: string;
    documentIds: readonly string[];
}

export function DocumentsScreen({ chatId, onBrowseFiles, onPreview }: DocumentsScreenProps) {
    return chatId ? <ChatLibrary chatId={chatId} onBrowseFiles={onBrowseFiles} onPreview={onPreview} /> : <Library chat={null} />;
}

function ChatLibrary({ chatId, onBrowseFiles, onPreview }: { chatId: string; onBrowseFiles?: (chatId: string) => void; onPreview?: DocumentsScreenProps['onPreview'] }) {
    const t = useT();
    const session = useSession(chatId);
    const title = session.data?.title || t.bare('docs.untitledChat');
    return <Library chat={{ id: chatId, title, name: `“${title}”`, documentIds: session.data?.document_ids ?? [] }} onBrowseFiles={onBrowseFiles} onPreview={onPreview} />;
}

function Library({ chat, onBrowseFiles, onPreview }: { chat: OpenChat | null; onBrowseFiles?: (chatId: string) => void; onPreview?: DocumentsScreenProps['onPreview'] }) {
    const t = useT();
    const { space } = useTheme();
    const query = useDocuments();
    const [target, setTarget] = useState<LibraryTarget>('chat');
    const action = useActionError();
    const [note, setNote] = useState<string | null>(null);
    const attachDocument = useAttachDocument();
    const nextIds = useNextChatDocs((s) => s.ids);
    const addNext = useNextChatDocs((s) => s.add);
    // The open chat when documents go there, else null: the next new chat.
    const targetChat = chat && target === 'chat' ? chat : null;
    const removal = useDocumentRemoval((r, error) => action.report(t.compose((say) => say('docs.deleteFailed', { name: r.name, error: describeErrorWith(error, say) }))));
    const cancel = useCancelIndexing();
    const reindex = useReindexDocument();
    const all = query.data?.documents ?? [];
    const documents = all.filter((d) => d.id !== removal.pending?.id);
    const view = useIndexingView(all);
    const stats = libraryStats(documents, t);
    const nameOf = (id: string) => all.find((d) => d.id === id)?.filename ?? id;
    const onCancel = (id: string) => {
        action.dismiss();
        cancel.mutate(id, { onError: (e) => action.report(t.compose((say) => say('docs.cancelFailed', { name: nameOf(id), error: describeErrorWith(e, say) }))) });
    };
    const onRetry = (id: string) => {
        action.dismiss();
        reindex.mutate(id, { onError: (e) => action.report(t.compose((say) => say('docs.retryFailed', { name: nameOf(id), error: describeErrorWith(e, say) }))) });
    };
    const onRemove = (id: string) => {
        action.dismiss();
        removal.remove({ id, name: nameOf(id) });
    };
    const onAttach = (id: string) => {
        action.dismiss();
        setNote(null);
        if (!targetChat) {
            addNext([id]);
            setNote(t('docs.willAttach', { name: nameOf(id) }));
            return;
        }
        attachDocument.mutate(
            { sessionId: targetChat.id, docId: id },
            {
                onSuccess: () => setNote(t('docs.attached', { name: nameOf(id), chat: targetChat.name })),
                onError: (e) => action.report(t.compose((say) => say('docs.attachFailed', { name: nameOf(id), chat: targetChat.name, error: describeErrorWith(e, say) }))),
            },
        );
    };
    const attachedIds = targetChat ? targetChat.documentIds : nextIds;
    const loadFailed = query.isError ? t.compose((say) => say('docs.loadFailed', { error: describeErrorWith(query.error, say) })) : null;
    const adder = <AddDocuments chatId={targetChat?.id ?? null} chatName={chat?.name ?? null} onStart={action.dismiss} onBrowse={onBrowseFiles} />;
    const retry = { label: t('docs.loadRetry'), onPress: () => void query.refetch() };

    return (
        <Screen header={<ScreenHeader title={t('docs.title')} />}>
            {query.data ? (
                <Stack gap="md" paddingX="lg" paddingY="sm">
                    <TargetChooser chatTitle={chat?.title ?? null} target={target} onChange={setTarget} />
                    {adder}
                </Stack>
            ) : null}
            {query.isPending ? <ListSkeleton rows={4} /> : null}
            {loadFailed && documents.length === 0 ? <ErrorState title={loadFailed} action={retry} /> : null}
            {query.data && documents.length === 0 ? <EmptyState title={t('docs.empty')} message={t('docs.emptyHintPhone')} /> : null}
            {removal.pending ? (
                <Stack paddingX="lg" paddingY="sm">
                    <Notice
                        tone="info"
                        role="note"
                        message={t('docs.removed', { name: isolate(removal.pending.name, 'ltr') })}
                        action={{ label: t('docs.undo'), onPress: removal.undo }}
                    />
                </Stack>
            ) : null}
            {documents.length > 0 ? (
                <List
                    data={documents}
                    keyExtractor={(d) => d.id}
                    accessibilityLabel={t('docs.title')}
                    contentContainerStyle={{ paddingBottom: space.xl }}
                    ListHeaderComponent={
                        <Stack gap="sm" paddingX="lg" paddingY="sm">
                            {note ? <Notice tone="success" role="note" message={note} onDismiss={() => setNote(null)} /> : null}
                            {loadFailed ? <Notice tone="danger" message={loadFailed} action={retry} /> : null}
                            {action.error ? (
                                <Row gap="sm" align="start">
                                    <Stack flex={1}>
                                        <Notice tone="danger" message={action.error} />
                                    </Stack>
                                    <IconButton icon={X} label={t('docs.dismissError')} onPress={action.dismiss} />
                                </Row>
                            ) : null}
                            {stats ? (
                                <Row gap="sm" wrap>
                                    <Text role="caption" tone="secondary">
                                        {stats.readable}
                                    </Text>
                                    {stats.unreadable ? (
                                        <Text role="caption" tone="danger">
                                            {stats.unreadable}
                                        </Text>
                                    ) : null}
                                    <Text role="caption" tone="secondary">
                                        {`· ${isolate(stats.size, 'ltr')}`}
                                    </Text>
                                </Row>
                            ) : null}
                        </Stack>
                    }
                    renderItem={({ item }) => (
                        <DocumentRow
                            document={item}
                            progress={view.progress[item.id]}
                            startedAt={view.startedAt[item.id]}
                            now={view.now}
                            retrying={reindex.isPending && reindex.variables === item.id}
                            attach={attachedIds.includes(item.id) ? undefined : { chatName: targetChat?.name ?? null, onAttach }}
                            onPreview={chat && onPreview ? (docId, name) => onPreview(chat.id, docId, name) : undefined}
                            onCancel={onCancel}
                            onRetry={onRetry}
                            onRemove={onRemove}
                        />
                    )}
                />
            ) : null}
        </Screen>
    );
}
