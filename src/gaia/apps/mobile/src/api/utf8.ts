// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Hermes does not guarantee a TextDecoder global, and the SSE body arrives as raw bytes.

export interface Utf8Decoder {
    /** Text for the complete characters so far; a character cut by the chunk edge waits for the next chunk. */
    push(chunk: Uint8Array): string;
    /** Whatever is left once the body ends; a cut-off character becomes U+FFFD. */
    end(): string;
}

const REPLACEMENT = '�';

function sequenceLength(lead: number): number {
    if (lead >= 0xc2 && lead <= 0xdf) return 2;
    if (lead >= 0xe0 && lead <= 0xef) return 3;
    if (lead >= 0xf0 && lead <= 0xf4) return 4;
    return 0;
}

function isContinuation(byte: number | undefined): byte is number {
    return byte !== undefined && byte >= 0x80 && byte <= 0xbf;
}

function decodeComplete(bytes: number[]): string {
    let out = '';
    for (let i = 0; i < bytes.length; ) {
        const lead = bytes[i]!;
        if (lead < 0x80) {
            out += String.fromCharCode(lead);
            i += 1;
            continue;
        }
        const length = sequenceLength(lead);
        let cp = length === 2 ? lead & 0x1f : length === 3 ? lead & 0x0f : lead & 0x07;
        let valid = length > 0;
        for (let k = 1; valid && k < length; k++) {
            const next = bytes[i + k];
            if (isContinuation(next)) cp = (cp << 6) | (next & 0x3f);
            else valid = false;
        }
        const minimum = length === 3 ? 0x800 : length === 4 ? 0x10000 : 0x80;
        const surrogate = cp >= 0xd800 && cp <= 0xdfff;
        if (!valid || cp < minimum || cp > 0x10ffff || surrogate) {
            out += REPLACEMENT;
            i += 1;
        } else {
            out += String.fromCodePoint(cp);
            i += length;
        }
    }
    return out;
}

export function createUtf8Decoder(): Utf8Decoder {
    let pending: number[] = [];
    return {
        push(chunk) {
            const bytes = pending.concat(Array.from(chunk));
            let cut = bytes.length;
            for (let back = 1; back <= 3 && back <= bytes.length; back++) {
                const byte = bytes[bytes.length - back]!;
                if (isContinuation(byte)) continue;
                if (sequenceLength(byte) > back) cut = bytes.length - back;
                break;
            }
            pending = bytes.slice(cut);
            return decodeComplete(bytes.slice(0, cut));
        },
        end() {
            const rest = pending;
            pending = [];
            return rest.length > 0 ? REPLACEMENT : '';
        },
    };
}
