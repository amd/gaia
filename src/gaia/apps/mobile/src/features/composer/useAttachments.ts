// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import { File } from 'expo-file-system';

import { composeWithLinks } from '@webui/utils/attachmentLinks';
import { isMediaFile } from '@webui/utils/mediaFiles';
import { uploadRefusalText } from '@webui/utils/uploadErrors';

import { GaiaApiError } from '@/api/errors';
import { useGaiaClient, requireClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';

import type { PickedFile } from './pickFiles';

export interface Attachment {
    id: string;
    name: string;
    uri: string;
    isImage: boolean;
    isMedia: boolean;
    /** Name, size and modified time: the same file picked again has the same key. */
    key: string;
    status: 'uploading' | 'uploaded' | 'failed';
    serverUrl?: string;
    error?: string;
}

/** Files attached to the message being composed, uploaded to the PC as soon as they are added. */
export interface Attachments {
    items: Attachment[];
    add(files: PickedFile[]): void;
    /** A chip for something that never reached the upload, such as a camera that is off. */
    addFailed(name: string, message: string): void;
    remove(id: string): void;
    clear(): void;
    uploading: boolean;
    /** At least one file has uploaded, so a message may go out with no words. */
    hasUploaded: boolean;
    /** The text with a link for each uploaded file. */
    compose(text: string): string;
}

export function useAttachments(): Attachments {
    const t = useT();
    const client = useGaiaClient();
    const [items, setItems] = useState<Attachment[]>([]);
    const controllers = useRef(new Map<string, AbortController>());
    const mounted = useRef(true);
    const current = useRef<Attachment[]>([]);
    useEffect(() => {
        current.current = items;
    }, [items]);
    useEffect(() => {
        mounted.current = true;
        const running = controllers.current;
        return () => {
            mounted.current = false;
            running.forEach((c) => c.abort());
        };
    }, []);

    const patch = useCallback((id: string, change: Partial<Attachment>) => {
        if (mounted.current) setItems((prev) => prev.map((a) => (a.id === id ? { ...a, ...change } : a)));
    }, []);

    const upload = useCallback(
        async (a: Attachment) => {
            const controller = new AbortController();
            controllers.current.set(a.id, controller);
            try {
                const blob = new File(a.uri);
                const result = await requireClient(client).uploadFile(blob, a.name, { signal: controller.signal });
                patch(a.id, { status: 'uploaded', serverUrl: result.url });
            } catch (err) {
                if (controller.signal.aborted) return;
                const body = err instanceof GaiaApiError ? err.body : null;
                const reason = typeof body?.status === 'string' ? body.status : null;
                patch(a.id, { status: 'failed', error: uploadRefusalText(reason, body, describeError(err, t), t) });
            } finally {
                controllers.current.delete(a.id);
            }
        },
        [client, patch, t],
    );

    const add = useCallback(
        (files: PickedFile[]) => {
            // The same file picked twice is one chip; a refused one may be tried again.
            const keyOf = (f: PickedFile) => (f.size === undefined ? f.uri : `${f.name}|${f.size}|${f.modified ?? ''}`);
            const seen = new Set<string>();
            const fresh = files
                .filter((f) => {
                    const key = keyOf(f);
                    if (seen.has(key) || current.current.some((p) => p.key === key && p.status !== 'failed')) return false;
                    seen.add(key);
                    return true;
                })
                .map((f): Attachment => ({
                    key: keyOf(f),
                    id: `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    name: f.name,
                    uri: f.uri,
                    isImage: !!f.mimeType?.startsWith('image/'),
                    isMedia: isMediaFile(f.name),
                    status: 'uploading',
                }));
            if (fresh.length === 0) return;
            setItems((prev) => [...prev.filter((p) => !fresh.some((a) => a.key === p.key)), ...fresh]);
            fresh.forEach((a) => void upload(a));
        },
        [upload],
    );

    const addFailed = useCallback((name: string, message: string) => {
        const id = `attach-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        setItems((prev) => [...prev, { id, key: id, name, uri: '', isImage: false, isMedia: false, status: 'failed', error: message }]);
    }, []);

    const remove = useCallback((id: string) => {
        controllers.current.get(id)?.abort();
        setItems((prev) => prev.filter((a) => a.id !== id));
    }, []);

    const clear = useCallback(() => {
        controllers.current.forEach((c) => c.abort());
        setItems([]);
    }, []);

    const compose = useCallback(
        (text: string) =>
            composeWithLinks(
                text,
                items.filter((a): a is Attachment & { serverUrl: string } => a.status === 'uploaded' && !!a.serverUrl),
                t('composer.mediaPrompt'),
            ),
        [items, t],
    );

    return {
        items,
        add,
        addFailed,
        remove,
        clear,
        uploading: items.some((a) => a.status === 'uploading'),
        hasUploaded: items.some((a) => a.status === 'uploaded'),
        compose,
    };
}
