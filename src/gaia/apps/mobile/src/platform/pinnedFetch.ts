// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { GaiaPinMismatchError, GaiaUploadUnsupportedError } from '@/api/errors';
import { createUtf8Decoder } from '@/api/utf8';

import { GaiaTransport } from '../../modules/gaia-transport';
import type { FetchRequestInit, FetchResponseLike } from './fetch.types';
import { encodeBody } from './uploadBody';

let lastId = 0;
let deviceGone: ((pcId: string) => void) | null = null;

/** Who hears that a paired PC refused this phone's token: it was unpaired there, or the PC was reset. */
export function onDeviceGone(handler: ((pcId: string) => void) | null): void {
    deviceGone = handler;
}

/** The listener's 401 for an unknown or revoked device token carries `WWW-Authenticate: Bearer`. */
export function reportIfDeviceGone(pcId: string, res: FetchResponseLike): FetchResponseLike {
    if (res.status === 401 && /^bearer\b/i.test(res.headers.get('www-authenticate') ?? '')) deviceGone?.(pcId);
    return res;
}

/** A native rejection as an Error the app can act on, with the native `code`; a pin mismatch is GaiaPinMismatchError. */
export function transportError(err: unknown): Error {
    const code = (err as { code?: unknown } | null)?.code;
    if (code === 'pin_mismatch') return new GaiaPinMismatchError({ cause: err });
    const message = err instanceof Error ? err.message : String(err);
    return Object.assign(new Error(message, { cause: err }), { code });
}

/** A request to a paired PC over TLS pinned to its certificate; the device token is added natively. */
export async function pinnedFetch(pcId: string, url: string, init: FetchRequestInit = {}): Promise<FetchResponseLike> {
    const { signal, body: sent } = init;
    const upload = sent !== undefined && typeof sent !== 'string' ? encodeBody(sent) : null;
    const { upload: sendUpload } = GaiaTransport;
    if (upload && !sendUpload) throw new GaiaUploadUnsupportedError();
    const id = `pinned-${++lastId}`;
    if (signal?.aborted) throw Object.assign(new Error('The request was canceled.'), { code: 'aborted' });
    const abort = () => GaiaTransport.cancel(id);
    signal?.addEventListener('abort', abort);
    const done = () => signal?.removeEventListener('abort', abort);

    let head;
    try {
        const method = init.method ?? 'GET';
        if (upload && sendUpload) {
            const headers = Object.fromEntries(Object.entries(init.headers ?? {}).filter(([name]) => name.toLowerCase() !== 'content-type'));
            if (upload.contentType) headers['Content-Type'] = upload.contentType;
            head = await sendUpload.call(GaiaTransport, id, pcId, method, url, headers, upload.segments);
        } else {
            head = await GaiaTransport.start(id, pcId, method, url, init.headers ?? {}, typeof sent === 'string' ? sent : null);
        }
    } catch (err) {
        done();
        throw transportError(err);
    }

    const body = new ReadableStream<Uint8Array>({
        async pull(controller) {
            try {
                const chunk = await GaiaTransport.read(id);
                if (chunk === null) {
                    done();
                    controller.close();
                } else {
                    controller.enqueue(chunk);
                }
            } catch (err) {
                done();
                controller.error(transportError(err));
            }
        },
        cancel() {
            done();
            GaiaTransport.cancel(id);
        },
    });

    return {
        ok: head.status >= 200 && head.status < 300,
        status: head.status,
        headers: { get: (name) => head.headers[name.toLowerCase()] ?? null },
        body,
        async text() {
            const decoder = createUtf8Decoder();
            const reader = body.getReader();
            let out = '';
            for (;;) {
                const { done: end, value } = await reader.read();
                if (end) break;
                out += decoder.push(value);
            }
            return out + decoder.end();
        },
    };
}
