// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { GaiaApiError, GaiaError } from '@/api/errors';
import { useAddDocument } from '@/data';
import { describeError, describeErrorWith, useT } from '@/i18n/useT';
import { pickDocuments, pickPhotos, type PickedFile } from '@/platform/pickers';
import { Button, Notice, ProgressLine, Row, Stack, Text } from '@/ui';
import { FileText, FolderOpen, Image as ImageIcon } from '@/ui/icons';
import { describeUploadError, unsupportedTypeMessage, type UploadErrorView } from '@webui/utils/uploadErrors.core';

import { useNextChatDocs } from './nextChatDocs';

// One line at a time: the indexing status, the result of the last add, or why it failed.
type AddLine =
    | { kind: 'busy'; name: string }
    | { kind: 'done'; message: string }
    | { kind: 'unreadable'; message: string }
    | { kind: 'failed'; error: UploadErrorView }
    | { kind: 'picker'; message: string }
    | { kind: 'unsupported'; message: string };

interface AddDocumentsProps {
    // The chat documents are attached to; null leaves them for the next new chat.
    chatId: string | null;
    chatName: string | null;
    // Called when an add starts, so the screen can clear its own library line.
    onStart?: () => void;
    // Opens the PC's file browser for the chat; unset, or with no chat, leaves the button out.
    onBrowse?: (chatId: string) => void;
}

export function AddDocuments({ chatId, chatName, onStart, onBrowse }: AddDocumentsProps) {
    const t = useT();
    const add = useAddDocument();
    const holdForNextChat = useNextChatDocs((s) => s.add);
    const [line, setLine] = useState<AddLine | null>(null);
    const busy = line?.kind === 'busy';

    async function addOne(file: PickedFile): Promise<AddLine> {
        const unsupported = unsupportedTypeMessage(file.name);
        if (unsupported) return { kind: 'failed', error: describeUploadError(t, { filename: file.name, error: unsupported }) };
        setLine({ kind: 'busy', name: file.name });
        try {
            const { document, attachError } = await add.mutateAsync({ file, chatId });
            const name = document.filename || file.name;
            if (document.indexing_status === 'failed') return { kind: 'unreadable', message: t('docs.notAdded', { name }) };
            if (attachError) {
                const error = t.compose((say) => say('docs.attachFailedAfterIndex', { chat: chatName ?? '', error: describeErrorWith(attachError, say) }));
                return { kind: 'failed', error: describeUploadError(t, { filename: file.name, error }) };
            }
            if (!chatId) holdForNextChat([document.id]);
            return { kind: 'done', message: chatId ? t('docs.attached', { name, chat: chatName ?? '' }) : t('docs.willAttach', { name }) };
        } catch (err) {
            // An app build without uploads can't send a file to a paired PC; the request wraps its error, so look at the cause too.
            const refusal = [err, err instanceof Error ? err.cause : null].find((e) => (e as { code?: string } | null)?.code === 'unsupported_body');
            if (refusal) return { kind: 'unsupported', message: describeError(refusal, t) };
            const api = err instanceof GaiaApiError ? err : null;
            const kind = typeof api?.body?.status === 'string' ? api.body.status : null;
            const error = kind === 'too_large' ? t('docs.tooLargeDetail', { max: String(api?.body?.max_mb) }) : describeError(err, t);
            return { kind: 'failed', error: describeUploadError(t, { filename: file.name, error, status: api?.status, kind }) };
        }
    }

    async function run(pick: () => Promise<PickedFile[]>) {
        let files: PickedFile[];
        try {
            files = await pick();
        } catch (err) {
            setLine({ kind: 'picker', message: describeError(err, t) });
            return;
        }
        if (files.length === 0) return;
        onStart?.();
        let result: AddLine | null = null;
        for (const file of files) {
            const next = await addOne(file);
            // The first failure stays on screen; later successes don't hide it.
            if (!result || result.kind === 'done') result = next;
        }
        setLine(result);
    }

    return (
        <Stack gap="sm">
            <Row gap="sm" wrap>
                <Button label={t('docs.addPhotos')} icon={ImageIcon} variant="secondary" disabled={busy} onPress={() => void run(pickPhotos)} />
                <Button label={t('docs.addFile')} icon={FileText} variant="secondary" disabled={busy} onPress={() => void run(pickDocuments)} />
            </Row>
            {onBrowse && chatId ? <Button label={t('chat.browseFiles')} icon={FolderOpen} variant="secondary" full disabled={busy} onPress={() => onBrowse(chatId)} /> : null}
            {line?.kind === 'busy' ? (
                <Stack gap="xs">
                    <Text role="caption" tone="secondary">
                        {t('docs.indexingFile', { name: line.name })}
                    </Text>
                    <ProgressLine />
                </Stack>
            ) : null}
            {line?.kind === 'done' ? <Notice tone="success" role="note" message={line.message} onDismiss={() => setLine(null)} /> : null}
            {line?.kind === 'unsupported' ? <Notice tone="warning" message={line.message} onDismiss={() => setLine(null)} /> : null}
            {line?.kind === 'unreadable' || line?.kind === 'picker' ? (
                <Notice tone="danger" message={line.message} onDismiss={() => setLine(null)} />
            ) : null}
            {line?.kind === 'failed' ? (
                <Notice
                    tone={line.error.icon === 'warning' ? 'warning' : 'danger'}
                    title={line.error.title}
                    message={line.error.detail}
                    onDismiss={() => setLine(null)}
                />
            ) : null}
        </Stack>
    );
}
