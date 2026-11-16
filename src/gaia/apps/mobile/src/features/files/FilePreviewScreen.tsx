// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { isolate } from '@webui/i18n/registry';

import { useT } from '@/i18n/useT';
import { Button, IconButton, Ltr, Row, Screen, Stack, Text } from '@/ui';
import { X } from '@/ui/icons';

import { Body, FilePreview, targetName, useLoadedFile } from './FilePreview';
import type { PreviewTarget } from './previewStore';

/** A PC file previewed on its own pushed screen: the desktop pane's loading, text, table and binary states, with Share through the existing preview sheet. */
export function FilePreviewScreen({ target, onClose }: { target: PreviewTarget; onClose: () => void }) {
    const t = useT();
    const state = useLoadedFile(target);
    const [sharing, setSharing] = useState(false);
    const name = targetName(target);
    const cached = state.status === 'ready' ? state.cached : null;
    const preview = state.status === 'ready' && state.loaded.kind === 'text' ? state.loaded.preview : null;
    const meta = preview
        ? [
              isolate(preview.size_display, 'ltr'),
              preview.total_lines ? t('fileBrowser.lines', { n: preview.total_lines }) : '',
              preview.columns ? t('fileBrowser.columnCount', { n: preview.columns.length }) : '',
              preview.row_count != null ? t('fileBrowser.rows', { n: preview.row_count }) : '',
          ].filter(Boolean)
        : [];

    return (
        <Screen
            header={
                <Stack paddingX="lg" paddingY="sm">
                    <Row gap="sm" accessibilityRole="header">
                        <Stack flex={1}>
                            <Text role="title" weight="semibold" numberOfLines={1} accessibilityLabel={t('fileBrowser.previewAria', { name })}>
                                {isolate(name, 'ltr')}
                            </Text>
                        </Stack>
                        <IconButton icon={X} label={t('fileBrowser.closePreview')} onPress={onClose} />
                    </Row>
                </Stack>
            }
            footer={
                cached ? (
                    <Button label={t('cards.share')} full onPress={() => setSharing(true)} />
                ) : undefined
            }
        >
            <Stack gap="md" paddingX="lg" paddingY="sm">
                {target.kind === 'path' ? <Ltr>{target.path}</Ltr> : null}
                {meta.length > 0 ? (
                    <Row gap="sm" wrap>
                        {meta.map((m) => (
                            <Text key={m} role="caption" tone="muted">
                                {m}
                            </Text>
                        ))}
                    </Row>
                ) : null}
                <Body target={target} state={state} />
            </Stack>
            {sharing ? <FilePreview target={target} onClose={() => setSharing(false)} /> : null}
        </Screen>
    );
}
