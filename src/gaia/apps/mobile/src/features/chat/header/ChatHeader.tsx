// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { MAX_SESSION_TITLE_CHARS } from '@webui/utils/constants';
import { isPlaceholderTitle } from '@webui/utils/sessionTitle';

import { useGaiaClient } from '@/data/GaiaClientProvider';
import { useRenameSession, useSession, useTogglePrivate } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { exportChat } from '@/features/share/exportChat';
import { describeError, useT } from '@/i18n/useT';
import { IconButton, Notice, Pressable, Row, Stack, Text, TextField } from '@/ui';
import { Activity, Check, Download, Eye, EyeOff, PanelRight, X } from '@/ui/icons';

import { DocsBar } from './DocsBar';
import { failureReasonWith } from './failureText';
import { ResourceStrip } from './ResourceStrip';

export interface ChatHeaderProps {
    sessionId: string;
    /** Shows the session-panel button when set; with none, the header has no such control. */
    onOpenSessionPanel?: () => void;
    /** Opens the document library for this chat; with none, the documents bar has no Manage link. */
    onManageDocuments?: () => void;
}

/** The chat's header: the title (tap to rename), the resource and private toggles, and the documents bar. */
export function ChatHeader({ sessionId, onOpenSessionPanel, onManageDocuments }: ChatHeaderProps) {
    const t = useT();
    const session = useSession(sessionId);
    const rename = useRenameSession();
    const togglePrivate = useTogglePrivate();
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('');
    const [resources, setResources] = useState(false);
    const client = useGaiaClient();
    const [error, setError] = useState<string | null>(null);
    const [exporting, setExporting] = useState(false);

    const title = session.data?.title;
    const isPrivate = !!session.data?.private;

    const startEdit = () => {
        setDraft(title ?? '');
        setEditing(true);
    };
    const save = () => {
        const next = draft.trim().slice(0, MAX_SESSION_TITLE_CHARS).trim();
        setEditing(false);
        // An untouched draft never cuts an old over-long title.
        if (!next || draft === title || next === title) return;
        setError(null);
        rename.mutate(
            { sessionId, title: next },
            { onError: (err) => setError(t.compose((say) => say('chat.renameFailed', { error: failureReasonWith(err, say) }))) },
        );
    };
    const flipPrivate = () => {
        setError(null);
        togglePrivate.mutate(sessionId, {
            onError: (err) => setError(t.compose((say) => say('chat.privateFailed', { error: failureReasonWith(err, say) }))),
        });
    };

    const runExport = () => {
        if (!client) return;
        setError(null);
        setExporting(true);
        exportChat(client, sessionId, title)
            .catch((err: unknown) => setError(describeError(err, t)))
            .finally(() => setExporting(false));
    };

    const titleNode = editing ? (
        <Row gap="xs">
            <Stack flex={1}>
                <TextField
                    value={draft}
                    onChangeText={(text) => setDraft(text.slice(0, MAX_SESSION_TITLE_CHARS))}
                    accessibilityLabel={t('chat.titleLabel')}
                    returnKeyType="done"
                    onSubmitEditing={save}
                    autoFocus
                    testID="chat-title-input"
                />
            </Stack>
            <IconButton icon={Check} size="sm" label={t('settings.advanced.save')} onPress={save} />
            <IconButton icon={X} size="sm" label={t('msg.editCancel')} onPress={() => setEditing(false)} />
        </Row>
    ) : (
        <Pressable onPress={startEdit} accessibilityLabel={t('row.rename')} testID="chat-title">
            <Text role="title" weight="semibold" numberOfLines={1}>
                {isPlaceholderTitle(title) ? t('sidebar.newChat') : title}
            </Text>
        </Pressable>
    );

    const end = editing ? undefined : (
        <Row>
            <IconButton
                icon={Activity}
                size="sm"
                label={t(resources ? 'resources.hide' : 'resources.show')}
                onPress={() => setResources(!resources)}
                testID="resources-toggle"
            />
            <IconButton
                icon={isPrivate ? EyeOff : Eye}
                size="sm"
                label={t(isPrivate ? 'composer.privateOff' : 'composer.privateOn')}
                onPress={flipPrivate}
                busy={togglePrivate.isPending}
                testID="private-toggle"
            />
            <IconButton
                icon={Download}
                size="sm"
                label={t('chat.exportLabel')}
                onPress={runExport}
                busy={exporting}
                testID="export-chat"
            />
            {onOpenSessionPanel ? (
                <IconButton
                    icon={PanelRight}
                    size="sm"
                    label={t('sp.show')}
                    onPress={onOpenSessionPanel}
                    testID="session-panel-toggle"
                />
            ) : null}
        </Row>
    );

    return (
        <>
            <ScreenHeader titleNode={session.data ? titleNode : undefined} end={end} />
            {error ? (
                <Stack paddingX="lg">
                    <Notice tone="danger" message={error} onDismiss={() => setError(null)} testID="header-error" />
                </Stack>
            ) : null}
            {resources ? <ResourceStrip onClose={() => setResources(false)} /> : null}
            <DocsBar sessionId={sessionId} onFailed={setError} onManage={onManageDocuments} />
        </>
    );
}
