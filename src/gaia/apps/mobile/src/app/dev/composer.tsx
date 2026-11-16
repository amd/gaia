// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Redirect, useLocalSearchParams } from 'expo-router';

import { devGalleryEnabled } from '@/features/dev/devGallery';
import { useState } from 'react';

import { MAX_MESSAGE_CHARS } from '@webui/utils/messageLimits';

import { COMPOSER_MENU_STATES, ComposerMenusGallery, type ComposerMenuState } from '@/features/composer/ComposerMenusGallery';
import { Composer, type Attachments, FolderChip, PermissionChip, PrivateChip } from '@/features/composer';
import { ControlChip } from '@/features/composer/ControlChip';
import { useT } from '@/i18n/useT';
import { Divider, Screen, Stack, UIProvider } from '@/ui';
import { Cpu } from '@/ui/icons';

const FIXTURE: Attachments['items'] = [
    { id: 'a', key: 'a', name: 'quarterly-report-final-v3.pdf', uri: '', isImage: false, isMedia: false, status: 'uploading' },
    { id: 'b', key: 'b', name: 'standup-recording.m4a', uri: '', isImage: false, isMedia: true, status: 'uploaded' },
    { id: 'c', key: 'c', name: 'notes.doc', uri: '', isImage: false, isMedia: false, status: 'failed', error: 'This file is empty.' },
    { id: 'd', key: 'd', name: 'IMG_2041.jpg', uri: 'https://picsum.photos/id/237/48', isImage: true, isMedia: false, status: 'uploaded' },
];
const noop = () => {};
const attachmentsFixture: Attachments = {
    items: FIXTURE,
    add: noop,
    addFailed: noop,
    remove: noop,
    clear: noop,
    uploading: true,
    hasUploaded: true,
    compose: (text) => text,
};

export default function ComposerGalleryRoute() {
    const t = useT();
    const { state } = useLocalSearchParams<{ state?: string }>();
    const [text, setText] = useState('');
    if (!devGalleryEnabled()) return <Redirect href="/" />;
    if (state && (COMPOSER_MENU_STATES as readonly string[]).includes(state)) return <ComposerMenusGallery state={state as ComposerMenuState} />;
    const folder = { path: '/Users/avery/Projects/gaia', name: 'gaia-notes', gaia_folder: false, choosable: false };
    const chipRow = (running = false, value?: string) => (
        <Composer
            onSend={noop}
            onStop={noop}
            running={running}
            value={value}
            onChange={value === undefined ? undefined : noop}
            leftControls={
                <>
                    <FolderChip folder={folder} />
                    <PermissionChip mode="ask" onChange={noop} />
                    <PrivateChip on={false} onChange={noop} />
                </>
            }
            rightControls={<ControlChip chip="model" icon={Cpu} menu label="Qwen3.6-35B-A3B-GGUF" testID="gallery-model-chip" />}
        />
    );
    return (
        <UIProvider>
            <Screen scroll keyboard="avoid">
                <Stack gap="md">
                    <Composer value={text} onChange={setText} onSend={noop} />
                    <Divider />
                    <Composer onSend={noop} attachments={attachmentsFixture} />
                    <Divider />
                    <Composer onSend={noop} running onStop={noop} />
                    <Divider />
                    <Composer onSend={noop} starting />
                    <Divider />
                    {chipRow()}
                    <Divider />
                    <Stack maxWidth={320}>{chipRow()}</Stack>
                    <Divider />
                    {chipRow(true)}
                    <Divider />
                    {chipRow(true, 'Summarize it')}
                    <Divider />
                    <Stack maxWidth={360}>{chipRow(true, 'Summarize it')}</Stack>
                    <Divider />
                    <Stack maxWidth={340}>{chipRow(true, 'Summarize it')}</Stack>
                    <Divider />
                    <Composer onSend={noop} disabledReason={t('composer.starting')} />
                    <Divider />
                    <Composer value={'x'.repeat(MAX_MESSAGE_CHARS + 1)} onChange={noop} onSend={noop} />
                </Stack>
            </Screen>
        </UIProvider>
    );
}
