// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The only schemes a link from content may open outside the app; the desktop shell's `link-policy.cjs` allows the same. */
export const ALLOWED_LINK_SCHEMES: ReadonlySet<string> = new Set(['http', 'https', 'mailto']);

export type LinkVerdict = { allowed: true; url: string } | { allowed: false; scheme: string | null };

const SCHEME = /^([a-z][a-z0-9+.-]*):/i;

/** Decides whether a link from a reply, a source or the server may leave the app. Control characters are dropped first, as browsers do, so `java\nscript:` is still `javascript`. */
export function checkLink(raw: string): LinkVerdict {
    const url = raw.replace(/[\u0000-\u001f\u007f]/g, '').trim();
    const scheme = SCHEME.exec(url)?.[1]?.toLowerCase() ?? null;
    if (!scheme) return { allowed: false, scheme: null };
    if (!ALLOWED_LINK_SCHEMES.has(scheme)) return { allowed: false, scheme };
    if (scheme !== 'mailto' && !/^https?:\/\/[^\s/?#]/i.test(url)) return { allowed: false, scheme };
    return { allowed: true, url };
}
