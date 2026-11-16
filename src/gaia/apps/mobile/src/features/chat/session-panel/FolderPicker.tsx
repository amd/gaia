// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { useBrowseFiles } from '@/data';
import { QUICK_LINK_LABELS, visibleEntries } from '@/features/files/fileBrowserModel';
import { useT } from '@/i18n/useT';
import { Button, Chip, Icon, IconButton, ListRow, Notice, Row, Stack, Switch, Text, TextField } from '@/ui';
import { ArrowUp, Folder, X } from '@/ui/icons';
import { isolate } from '@webui/i18n/registry';

import { failureReason } from '../header/failureText';

interface FolderPickerProps {
    sessionId: string;
    onPick(path: string): void;
    onClose(): void;
    /** Why adding the chosen folder failed, shown under the list. */
    error?: unknown;
    busy?: boolean;
}

/** Pick a folder on the PC to give the chat, by tapping through folders or typing its path. */
export function FolderPicker({ sessionId, onPick, onClose, error, busy }: FolderPickerProps) {
    const t = useT();
    const [path, setPath] = useState<string | undefined>(undefined);
    const [typed, setTyped] = useState('');
    const [showHidden, setShowHidden] = useState(false);
    const browse = useBrowseFiles(sessionId, path);
    const listing = browse.data;

    const [seen, setSeen] = useState<string | undefined>(undefined);
    if (listing && listing.current_path !== seen) {
        setSeen(listing.current_path);
        setTyped(listing.current_path);
    }

    const all = listing?.entries.filter((e) => e.type === 'folder') ?? [];
    const folders = visibleEntries(all, showHidden);
    const go = () => {
        if (typed.trim()) setPath(typed.trim());
    };
    return (
        <Stack gap="md" testID="folder-picker">
            <Row gap="sm">
                <Stack flex={1}>
                    <Text weight="semibold" accessibilityRole="header" accessibilityLabel={t('pick.label')}>
                        {t('pick.title')}
                    </Text>
                </Stack>
                <IconButton icon={X} size="sm" variant="ghost" label={t('sp.closeShort')} onPress={onClose} testID="folder-picker-close" />
            </Row>
            <Text role="caption" tone="muted">{t('pick.note')}</Text>
            <Row gap="sm" align="end">
                <Stack flex={1}>
                    <TextField
                        value={typed}
                        onChangeText={setTyped}
                        placeholder={t.bare('pick.path')}
                        accessibilityLabel={t('pick.path')}
                        onSubmitEditing={go}
                        returnKeyType="go"
                    />
                </Stack>
                <Button label={t('pick.go')} size="sm" variant="secondary" onPress={go} />
            </Row>
            {listing?.quick_links?.length ? (
                <Row gap="sm" wrap>
                    {listing.quick_links.map((link) => (
                        <Chip
                            key={link.path}
                            label={QUICK_LINK_LABELS[link.icon ?? ''] ? t(QUICK_LINK_LABELS[link.icon ?? '']!) : link.name}
                            icon={Folder}
                            onPress={() => setPath(link.path)}
                        />
                    ))}
                </Row>
            ) : null}
            <Switch value={showHidden} onValueChange={setShowHidden} label={t('pick.showHidden')} />
            <Stack gap="xs" accessibilityRole="list">
                {listing?.parent_path ? (
                    <ListRow
                        title={t('pick.up')}
                        leading={<Icon icon={ArrowUp} size="sm" tone="muted" />}
                        onPress={() => setPath(listing.parent_path ?? undefined)}
                    />
                ) : null}
                {folders.map((f) => (
                    <ListRow key={f.path} title={isolate(f.name, 'ltr')} leading={<Icon icon={Folder} size="sm" tone="muted" />} onPress={() => setPath(f.path)} />
                ))}
                {browse.isPending ? <Text tone="muted">{t('pick.loading')}</Text> : null}
                {listing && folders.length === 0 ? <Text tone="muted">{t(all.length > 0 ? 'pick.onlyHidden' : 'pick.none')}</Text> : null}
            </Stack>
            {browse.error ? <Notice tone="danger" message={failureReason(browse.error, t)} /> : null}
            {listing?.folder_refusal ? <Notice tone="warning" message={listing.folder_refusal} /> : null}
            {error ? <Notice tone="danger" message={failureReason(error, t)} /> : null}
            <Row gap="sm">
                <Button label={t('sp.cancel')} variant="ghost" onPress={onClose} />
                <Button
                    label={t('pick.use')}
                    variant="primary"
                    disabled={!listing || !!listing.folder_refusal || busy}
                    onPress={() => listing && onPick(listing.current_path)}
                    testID="folder-picker-use"
                />
            </Row>
        </Stack>
    );
}
