// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaUploadUnsupportedError } from '@/api/errors';

/** A piece of a request body for the pinned transport: text sent as UTF-8, or a file on this phone sent from disk. */
export type BodySegment = { text: string } | { file: string };

export interface EncodedBody {
    /** Null when the body is one file of no known type. */
    contentType: string | null;
    segments: BodySegment[];
}

interface LocalFile {
    uri: string;
    name?: unknown;
    type?: unknown;
}

function localFile(value: unknown): LocalFile | null {
    const uri = (value as { uri?: unknown } | null)?.uri;
    return typeof uri === 'string' && uri.startsWith('file://') ? (value as LocalFile) : null;
}

function nonEmpty(value: unknown): string | null {
    return typeof value === 'string' && value !== '' ? value : null;
}

// The escaping browsers apply to a part's name and filename.
function quoted(value: string): string {
    return value.replace(/\r/g, '%0D').replace(/\n/g, '%0A').replace(/"/g, '%22');
}

function randomBoundary(): string {
    let hex = '';
    for (let i = 0; i < 32; i++) hex += Math.floor(Math.random() * 16).toString(16);
    return `----GaiaPhoneFormBoundary${hex}`;
}

function encodeForm(form: FormData, boundary: string): EncodedBody {
    const segments: BodySegment[] = [];
    let text = '';
    const entries = (form as unknown as { entries(): Iterable<[string, unknown]> }).entries();
    for (const [name, value] of entries) {
        text += `--${boundary}\r\nContent-Disposition: form-data; name="${quoted(name)}"`;
        if (typeof value === 'string') {
            text += `\r\n\r\n${value}\r\n`;
            continue;
        }
        const file = localFile(value);
        if (!file) throw new GaiaUploadUnsupportedError();
        const filename = nonEmpty(file.name) ?? nonEmpty(file.uri.split('/').pop()) ?? 'file';
        text += `; filename="${quoted(filename)}"\r\nContent-Type: ${nonEmpty(file.type) ?? 'application/octet-stream'}\r\n\r\n`;
        segments.push({ text }, { file: file.uri });
        text = '\r\n';
    }
    segments.push({ text: `${text}--${boundary}--\r\n` });
    return { contentType: `multipart/form-data; boundary=${boundary}`, segments };
}

/**
 * A file or multipart form as segments the pinned transport sends, so files go from disk without passing through
 * JavaScript. GaiaUploadUnsupportedError for a file that only exists in memory (a Blob with no `file://` URI).
 */
export function encodeBody(body: Blob | FormData, boundary: string = randomBoundary()): EncodedBody {
    if (typeof FormData !== 'undefined' && body instanceof FormData) return encodeForm(body, boundary);
    const file = localFile(body);
    if (!file) throw new GaiaUploadUnsupportedError();
    return { contentType: nonEmpty(file.type), segments: [{ file: file.uri }] };
}
