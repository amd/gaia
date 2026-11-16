// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Image } from 'expo-image';
import { useCallback, useEffect, useState } from 'react';

import { isolate } from '@webui/i18n/registry';
import type { FilePreviewResponse } from '@/api/client.types';
import { useGaiaClient } from '@/data/GaiaClientProvider';
import { Markdown } from '@/features/chat/markdown/Markdown';
import { shareFile } from '@/features/share/shareFile';
import { describeError, useT } from '@/i18n/useT';
import { Button, Ltr, Notice, ScrollBox, Row, Sheet, Spinner, Stack, Text } from '@/ui';

import { cachePcFile, extensionOf, type CachedFile } from './cache';
import { usePreviewStore, type PreviewTarget } from './previewStore';

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']);
const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown', '.mdx']);
const BODY_MAX_HEIGHT = 420;

type Loaded =
    | { kind: 'text'; preview: FilePreviewResponse }
    | { kind: 'image'; uri: string }
    | { kind: 'file' };

export type State = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; loaded: Loaded; cached: CachedFile | null };

function baseName(path: string): string {
    return path.split(/[\\/]/).pop() || path;
}

export function targetName(target: PreviewTarget): string {
    return target.kind === 'path' ? baseName(target.path) : target.name || 'document';
}

/** The file's bytes in the cache (for Share and, for an image, for showing), and the cache entry removed when the preview closes. */
export function useLoadedFile(target: PreviewTarget): State {
    const client = useGaiaClient();
    const [state, setState] = useState<State>({ status: 'loading' });
    useEffect(() => {
        let live = true;
        let cached: CachedFile | null = null;
        (async () => {
            if (!client) throw new Error('No PC is paired.');
            const name = targetName(target);
            const ext = extensionOf(name);
            if (target.kind === 'document') {
                const bytes = await client.getDocumentBytes(target.docId, target.sessionId);
                cached = await cachePcFile(`document:${target.docId}`, name, bytes);
                return { loaded: (IMAGE_EXTENSIONS.has(ext) ? { kind: 'image', uri: cached.uri } : { kind: 'file' }) as Loaded, cached };
            }
            if (IMAGE_EXTENSIONS.has(ext)) {
                const bytes = await client.getImageBytes(target.path, target.sessionId);
                cached = await cachePcFile(`path:${target.path}`, name, bytes);
                return { loaded: { kind: 'image', uri: cached.uri } as Loaded, cached };
            }
            const preview = await client.getFilePreview(target.path, target.sessionId);
            if (preview.is_text) {
                // The PC serves a phone no bytes of a text file, so Share carries the lines shown.
                cached = await cachePcFile(`path:${target.path}`, name, new TextEncoder().encode((preview.preview_lines ?? []).join('\n')));
            }
            return { loaded: (preview.is_text ? { kind: 'text', preview } : { kind: 'file' }) as Loaded, cached };
        })().then(
            (result) => {
                if (live) setState({ status: 'ready', ...result });
                else result.cached?.remove();
            },
            (error: unknown) => {
                cached?.remove();
                if (live) setState({ status: 'error', error });
            },
        );
        return () => {
            live = false;
            cached?.remove();
        };
    }, [client, target]);
    return state;
}

function TextBody({ preview, name }: { preview: FilePreviewResponse; name: string }) {
    const t = useT();
    const lines = preview.preview_lines ?? [];
    const total = preview.total_lines ?? lines.length;
    const markdown = MARKDOWN_EXTENSIONS.has(extensionOf(name));
    return (
        <Stack gap="sm">
            {preview.columns ? (
                <Stack gap="xs">
                    <Text role="caption" tone="muted">
                        {t('fileBrowser.columnsLabel')}
                    </Text>
                    <Text role="label" mono dir="ltr" selectable>
                        {preview.columns.join(', ')}
                    </Text>
                    {preview.row_count != null ? <Text role="caption" tone="muted">{t('fileBrowser.rows', { n: preview.row_count })}</Text> : null}
                </Stack>
            ) : null}
            <ScrollBox maxHeight={BODY_MAX_HEIGHT}>
                {markdown ? (
                    <Markdown text={lines.join('\n')} />
                ) : (
                    <Text role="label" mono dir="ltr" selectable>
                        {lines.join('\n')}
                    </Text>
                )}
            </ScrollBox>
            {total > lines.length ? (
                <Text role="caption" tone="muted">
                    {t('fileBrowser.firstLines', { shown: lines.length, total })}
                </Text>
            ) : null}
        </Stack>
    );
}

export function Body({ target, state }: { target: PreviewTarget; state: State }) {
    const t = useT();
    const name = targetName(target);
    if (state.status === 'loading') {
        return (
            <Row gap="sm" justify="center">
                <Spinner />
                <Text tone="secondary">{t('fileBrowser.previewLoading')}</Text>
            </Row>
        );
    }
    if (state.status === 'error') return <Notice tone="danger" message={describeError(state.error, t)} />;
    const { loaded } = state;
    if (loaded.kind === 'text') return <TextBody preview={loaded.preview} name={name} />;
    if (loaded.kind === 'image') return <Image source={{ uri: loaded.uri }} style={{ width: '100%', height: BODY_MAX_HEIGHT }} contentFit="contain" accessibilityLabel={name} />;
    return <Text tone="secondary">{t('fileBrowser.binary')}</Text>;
}

/** A PC file or cited document, previewed in a sheet: text, Markdown, CSV columns or an image, with Share from the cached copy. */
export function FilePreview({ target, onClose }: { target: PreviewTarget | null; onClose: () => void }) {
    if (!target) return <Sheet open={false} onClose={onClose} />;
    return <OpenPreview key={target.kind === 'path' ? target.path : `${target.docId}#${target.page ?? ''}`} target={target} onClose={onClose} />;
}

function OpenPreview({ target, onClose }: { target: PreviewTarget; onClose: () => void }) {
    const t = useT();
    const state = useLoadedFile(target);
    const [shareError, setShareError] = useState<unknown>(null);
    const cached = state.status === 'ready' ? state.cached : null;
    const share = useCallback(async () => {
        if (!cached) return;
        setShareError(null);
        try {
            await shareFile(cached.uri);
        } catch (error) {
            setShareError(error);
        }
    }, [cached]);
    const name = targetName(target);
    return (
        <Sheet
            open
            onClose={onClose}
            title={isolate(name, 'ltr')}
            footer={cached ? <Button label={t('cards.share')} onPress={share} full /> : undefined}
        >
            <Stack gap="md">
                {target.kind === 'path' ? <Ltr>{target.path}</Ltr> : null}
                <Body target={target} state={state} />
                {shareError ? <Notice tone="danger" message={t('cards.shareFailed', { error: describeError(shareError, t) })} /> : null}
            </Stack>
        </Sheet>
    );
}

/** Mounted once, inside the screen that shows chats: opens whatever `openPcFile` asked for. */
export function FilePreviewHost() {
    const target = usePreviewStore((s) => s.target);
    const close = usePreviewStore((s) => s.close);
    return <FilePreview target={target} onClose={close} />;
}
