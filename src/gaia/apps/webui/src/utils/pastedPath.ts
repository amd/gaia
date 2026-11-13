// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Turn a pasted path into a plain one: drops Windows "Copy as path" quotes and file:// prefixes. */
export function cleanPastedPath(raw: string): string {
    let p = raw.trim();
    const quoted = p.match(/^(["'])(.*)\1$/);
    if (quoted) p = quoted[2].trim();
    if (/^file:\/\//i.test(p)) {
        p = p.replace(/^file:\/\/(localhost)?/i, '');
        try {
            p = decodeURIComponent(p);
        } catch {
            // Not valid percent-encoding: keep the text as typed.
        }
        // file:///C:/x leaves "/C:/x"; the drive path needs no leading slash.
        p = p.replace(/^\/([A-Za-z]:[\\/])/, '$1');
    }
    return p;
}
