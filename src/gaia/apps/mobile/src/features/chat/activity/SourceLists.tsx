// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentStep, RetrievalChunk } from '@webui/types';
import { isolate } from '@webui/i18n/registry';
import { useState } from 'react';

import { useT } from '@/i18n/useT';
import { Button, Disclosure, Icon, Row, Stack, Text } from '@/ui';
import { FileText, Folder } from '@/ui/icons';

const PREVIEW_COUNT = 5;

/** A path on the PC; the phone can't open it, so it shows as text. */
function PathLink({ label }: { label: string }) {
    return (
        <Text role="label" dir="ltr" tone="secondary">
            {isolate(label, 'ltr')}
        </Text>
    );
}

function Chunk({ chunk }: { chunk: RetrievalChunk }) {
    const t = useT();
    const [open, setOpen] = useState(false);
    const title = [
        chunk.source ? isolate(chunk.source, 'ltr') : '',
        chunk.page != null ? t('activity.chunkPage', { page: chunk.page }) : '',
        chunk.score != null && chunk.score > 0 ? isolate(chunk.score.toFixed(2), 'ltr') : '',
    ]
        .filter(Boolean)
        .join(' · ');
    return (
        <Disclosure title={title} open={open} onToggle={() => setOpen((v) => !v)} leading={<Icon icon={FileText} size="sm" tone="accent" />}>
            <Stack gap="xs">
                {chunk.sourcePath ? <PathLink label={chunk.sourcePath} /> : null}
                <Text role="label" dir="auto" tone="secondary" selectable>
                    {chunk.content}
                </Text>
            </Stack>
        </Disclosure>
    );
}

export function ChunksView({ chunks }: { chunks: RetrievalChunk[] }) {
    const t = useT();
    return (
        <Stack gap="xs">
            <Text role="caption" tone="muted" weight="medium">
                {t('activity.chunks', { n: chunks.length })}
            </Text>
            {chunks.map((chunk) => (
                <Chunk key={chunk.id} chunk={chunk} />
            ))}
        </Stack>
    );
}

type FileEntry = Record<string, unknown>;

function entryName(file: FileEntry, i: number, t: ReturnType<typeof useT>): string {
    const name = file.name || file.filename || file.file_name;
    if (name) return String(name);
    if (file.file_path) return String(file.file_path).replace(/\\/g, '/').split('/').pop() || t('activity.files.file', { n: i + 1 });
    return t('activity.files.file', { n: i + 1 });
}

export function FileListView({ fileList }: { fileList: NonNullable<AgentStep['fileList']> }) {
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const shown = expanded ? fileList.files : fileList.files.slice(0, PREVIEW_COUNT);
    const remaining = fileList.total - PREVIEW_COUNT;
    return (
        <Stack gap="xs">
            {shown.map((file, i) => {
                const name = entryName(file, i, t);
                const isFolder = file.kind === 'folder';
                const path = typeof file.file_path === 'string' && !isFolder ? file.file_path : null;
                return (
                    <Row key={i} gap="sm" wrap>
                        <Icon icon={isFolder ? Folder : FileText} size="sm" tone="muted" />
                        {path ? <PathLink label={name} /> : <Text role="label" dir="ltr">{isolate(name, 'ltr')}</Text>}
                        {file.directory ? (
                            <Text role="caption" tone="muted" dir="ltr">
                                {isolate(String(file.directory), 'ltr')}
                            </Text>
                        ) : null}
                    </Row>
                );
            })}
            {remaining > 0 && !expanded ? <Button variant="ghost" size="sm" label={t('activity.files.more', { n: remaining })} onPress={() => setExpanded(true)} /> : null}
            {expanded && fileList.files.length < fileList.total ? (
                <Text role="caption" tone="muted">
                    {t('activity.files.showing', { shown: fileList.files.length, total: fileList.total })}
                </Text>
            ) : null}
        </Stack>
    );
}
