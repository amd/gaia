// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useMemo, useState } from 'react';

import { DATA_IMAGE_RE, parseMediaSrc } from '@webui/utils/renderCards.core';

import { UI_HEADERS } from '@/api/constants';
import { GaiaApiError } from '@/api/errors';
import { platformFetch } from '@/platform/fetch';
import { useConnectionStore } from '@/state/connection';

export interface LoadedImage {
    /** A `data:image/...;base64,` URI the card draws. */
    uri: string;
    mime: string;
    base64: string;
}

export type ImageState = { status: 'loading' } | { status: 'failed'; error: string; refused?: boolean } | { status: 'ready'; image: LoadedImage };

const RASTER_MIME = /^image\/(png|jpe?g|gif|webp)$/;
const MAX_IMAGE_BYTES = 25 * 1024 * 1024;

export function fromDataUrl(src: string): LoadedImage {
    const mime = src.slice('data:'.length, src.indexOf(';'));
    return { uri: src, mime, base64: src.slice(src.indexOf(',') + 1) };
}

function toBase64(bytes: Uint8Array): string {
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(binary);
}

/** Reads a generated image from the paired PC, over the app's own transport so a pinned connection and its token apply. */
export async function fetchGeneratedImage(baseUrl: string, token: string | null | undefined, name: string): Promise<LoadedImage> {
    const url = `${baseUrl.replace(/\/+$/, '')}/api/media/image/${name}`;
    const res = await platformFetch(url, { headers: { ...UI_HEADERS, ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
    if (res.status === 403) throw new GaiaApiError(403, 'The PC running GAIA refused the image to this phone');
    if (!res.ok) throw new Error(`GAIA answered ${res.status} for the image`);
    const mime = (res.headers.get('content-type') ?? '').split(';')[0].trim().toLowerCase();
    if (!RASTER_MIME.test(mime)) throw new Error(`GAIA sent "${mime || 'no type'}", which isn't a PNG, JPEG, GIF or WebP image`);
    if (!res.body) throw new Error('GAIA sent no image data');
    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > MAX_IMAGE_BYTES) {
            await reader.cancel();
            throw new Error(`The image is larger than ${MAX_IMAGE_BYTES / (1024 * 1024)} MB`);
        }
        chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let at = 0;
    for (const chunk of chunks) {
        bytes.set(chunk, at);
        at += chunk.length;
    }
    const base64 = toBase64(bytes);
    return { uri: `data:${mime};base64,${base64}`, mime, base64 };
}

/** The image of a validated card `src`: a data URL as given, or a generated file loaded from the paired PC. */
export function useCardImage(src: string): ImageState {
    const connection = useConnectionStore((s) => s.connection);
    const baseUrl = connection?.baseUrl;
    const token = connection?.token;
    const isData = DATA_IMAGE_RE.test(src);
    const name = isData ? null : parseMediaSrc('image', src);
    const key = `${baseUrl}|${token}|${src}`;
    const [loaded, setLoaded] = useState<{ key: string; state: ImageState } | null>(null);
    useEffect(() => {
        if (!name || !baseUrl) return;
        let live = true;
        fetchGeneratedImage(baseUrl, token, name).then(
            (image) => live && setLoaded({ key, state: { status: 'ready', image } }),
            (e: unknown) => {
                console.error(`Loading the image ${name} from ${baseUrl} failed`, e);
                if (live) setLoaded({ key, state: { status: 'failed', error: e instanceof Error ? e.message : String(e), refused: e instanceof GaiaApiError && e.status === 403 } });
            },
        );
        return () => {
            live = false;
        };
    }, [name, baseUrl, token, key]);
    const dataImage = useMemo(() => (isData ? fromDataUrl(src) : null), [isData, src]);
    if (dataImage) return { status: 'ready', image: dataImage };
    if (!name) return { status: 'failed', error: 'Not a GAIA image reference' };
    if (!baseUrl) return { status: 'failed', error: 'No PC is paired. Pair this phone with GAIA first.' };
    return loaded?.key === key ? loaded.state : { status: 'loading' };
}
